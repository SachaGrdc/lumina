"""
dnd.py — glisser-déposer dans la barre latérale (mixin de LuminaWindow).

  * note    -> note    : crée un dossier parent (nommé d'après la cible) et y range les deux ;
  * note    -> dossier : déplace la note dans le dossier ;
  * dossier -> dossier : déplace le dossier (les cycles sont refusés) ;
  * élément -> en-tête de la sidebar ou zone vide sous la liste : remonte à la racine.
Un dossier replié s'ouvre tout seul si on le survole quelques instants.

Fichiers externes (gestionnaire de fichiers : Nautilus, Explorateur…) : les
.md / .txt déposés sont COPIÉS dans le dossier survolé (dossier parent si l'on
survole une note, racine pour l'en-tête ou la zone vide).

Les deux cas partagent les mêmes cibles de dépôt : un glissement interne
transporte un chemin (str), un glissement externe une Gdk.FileList.

Le mixin attend de la fenêtre : `store`, `toast()`, `refresh_tree()`, `_flush()`,
`_find_row()`, `_path_moved()`, `_expanded_snapshot()`, `_remap_paths()` et `open_note()`.
"""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

from i18n import _  # noqa: E402
from storage import Node, StorageError  # noqa: E402

SPRING_DELAY_MS = 700    # survol d'un dossier replié pendant un glissement -> il s'ouvre

# Type de dépôt -> classe CSS appliquée à la cible survolée (voir styles.py)
_HL_CLASSES = {
    "move": "lumina-drop-into",
    "group": "lumina-drop-group",
    "root": "lumina-drop-root",
}


class DragDropMixin:
    def _init_dnd(self):
        self._drag_src: Path | None = None   # élément en cours de glissement
        self._drag_src_is_dir = False
        self._drag_widget: Gtk.Widget | None = None
        self._hl_widget: Gtk.Widget | None = None   # cible actuellement en surbrillance
        self._hl_kind: str | None = None
        self._hl_icon: str | None = None
        self._spring_source = 0
        self._spring_path: Path | None = None

    def _attach_row_dnd(self, expander: Gtk.Widget, list_item):
        """Chaque ligne est à la fois une SOURCE de glissement et une CIBLE de dépôt."""
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare, list_item)
        source.connect("drag-begin", self._on_drag_begin, list_item)
        source.connect("drag-end", self._on_drag_end)
        expander.add_controller(source)

        target = self._new_drop_target()
        target.connect("enter", self._on_row_drop_motion, list_item)
        target.connect("motion", self._on_row_drop_motion, list_item)
        target.connect("leave", self._on_row_drop_leave, list_item)
        target.connect("drop", self._on_row_drop, list_item)
        expander.add_controller(target)

    @staticmethod
    def _new_drop_target() -> Gtk.DropTarget:
        """Cible acceptant un élément de la sidebar (chemin) ou des fichiers externes."""
        target = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        target.set_gtypes([Gdk.FileList, GObject.TYPE_STRING])   # FileList d'abord
        return target

    def _is_external(self, target: Gtk.DropTarget) -> bool:
        """Vrai si le glissement en cours apporte des fichiers depuis l'extérieur de Lumina."""
        drop = target.get_current_drop()
        return (self._drag_src is None and drop is not None
                and drop.get_formats().contain_gtype(Gdk.FileList))

    def _dnd_row_unbound(self, expander: Gtk.Widget):
        """La ligne est recyclée par la ListView : on nettoie ses effets visuels."""
        if expander is self._hl_widget:
            self._clear_highlight()
        expander.remove_css_class("lumina-dragging")

    # ================================================================== Glisser-déposer

    # --- source

    def _on_drag_prepare(self, source, x, y, list_item):
        row = list_item.get_item()
        if row is None:
            return None
        node = row.get_item().node
        # L'aperçu qui suit le curseur est un instantané de la ligne.
        source.set_icon(Gtk.WidgetPaintable.new(list_item.get_child()), int(x), int(y))
        # La charge utile est le chemin ; la validité est vérifiée à chaque survol.
        return Gdk.ContentProvider.new_for_value(
            GObject.Value(GObject.TYPE_STRING, str(node.path)))

    def _on_drag_begin(self, _source, _drag, list_item):
        row = list_item.get_item()
        node = row.get_item().node
        self._drag_src = node.path
        self._drag_src_is_dir = node.is_dir
        self._drag_widget = list_item.get_child()
        self._drag_widget.add_css_class("lumina-dragging")

    def _on_drag_end(self, _source, _drag, _delete_data):
        if self._drag_widget is not None:
            self._drag_widget.remove_css_class("lumina-dragging")
        self._drag_widget = None
        self._drag_src = None
        self._clear_highlight()
        self._cancel_spring()

    # --- règles de dépôt

    def _drop_kind(self, target: Node | None) -> str | None:
        """
        Que se passerait-il si on déposait l'élément glissé sur `target` ?
          "move"  : déplacement dans le dossier `target`
          "group" : création d'un dossier regroupant les deux notes
          "root"  : déplacement à la racine (target = None)
          None    : dépôt refusé
        """
        src = self._drag_src
        if src is None:  # glissement qui ne vient pas de Lumina (texte externe…)
            return None
        if target is None:
            return "root" if src.parent != self.store.root else None
        if target.path == src:
            return None
        if target.is_dir:
            if src.parent == target.path:  # déjà dedans
                return None
            if self._drag_src_is_dir and src in target.path.parents:  # cycle
                return None
            return "move"
        return None if self._drag_src_is_dir else "group"

    # --- cibles : lignes

    def _on_row_drop_motion(self, target, _x, _y, list_item):
        row = list_item.get_item()
        if row is None:
            return 0
        node = row.get_item().node
        if self._is_external(target):  # import : dans le dossier survolé, ou celui de la note
            if node.is_dir:
                self._set_highlight(list_item.get_child(), "move")
                if not row.get_expanded():
                    self._schedule_spring(node.path)
            else:
                self._clear_highlight()
                self._cancel_spring()
            return Gdk.DragAction.COPY
        kind = self._drop_kind(node)
        if kind is None:
            self._clear_highlight()
            self._cancel_spring()
            return 0  # curseur « interdit »

        self._set_highlight(list_item.get_child(), kind)
        if kind == "move" and not row.get_expanded():
            self._schedule_spring(node.path)  # ouverture automatique du dossier
        else:
            self._cancel_spring()
        return Gdk.DragAction.MOVE

    def _on_row_drop_leave(self, _target, list_item):
        if list_item.get_child() is self._hl_widget:
            self._clear_highlight()
        self._cancel_spring()

    def _on_row_drop(self, _target, value, _x, _y, list_item):
        row = list_item.get_item()
        self._clear_highlight()
        self._cancel_spring()
        if row is None:
            return False
        node = row.get_item().node
        if isinstance(value, Gdk.FileList):
            dest = node.path if node.is_dir else node.path.parent
            GLib.idle_add(self._perform_import, value.get_files(), dest)
            return True
        src = self._drag_src
        if src is None or str(value) != str(src):
            return False
        kind = self._drop_kind(node)
        if kind is None:
            return False
        # L'opération est différée : on ne modifie pas le modèle (donc la ligne
        # source) pendant que GTK finalise encore le glissement.
        GLib.idle_add(self._perform_drop, kind, src, node.path)
        return True

    # --- cibles : racine

    def _add_root_drop_target(self, widget: Gtk.Widget):
        widget.add_css_class("lumina-root-zone")
        target = self._new_drop_target()
        target.connect("enter", self._on_root_drop_motion)
        target.connect("motion", self._on_root_drop_motion)
        target.connect("leave", self._on_root_drop_leave)
        target.connect("drop", self._on_root_drop)
        widget.add_controller(target)

    def _on_root_drop_motion(self, target, _x, _y):
        if self._is_external(target):
            self._cancel_spring()
            self._set_highlight(target.get_widget(), "root")
            return Gdk.DragAction.COPY
        if self._drop_kind(None) != "root":
            self._clear_highlight()
            return 0
        self._cancel_spring()
        self._set_highlight(target.get_widget(), "root")
        return Gdk.DragAction.MOVE

    def _on_root_drop_leave(self, target):
        if target.get_widget() is self._hl_widget:
            self._clear_highlight()

    def _on_root_drop(self, _target, value, _x, _y):
        self._clear_highlight()
        if isinstance(value, Gdk.FileList):
            GLib.idle_add(self._perform_import, value.get_files(), self.store.root)
            return True
        src = self._drag_src
        if src is None or str(value) != str(src) or self._drop_kind(None) != "root":
            return False
        GLib.idle_add(self._perform_drop, "root", src, None)
        return True

    # --- retours visuels

    @staticmethod
    def _row_icon(expander: Gtk.Widget) -> Gtk.Image:
        return expander.get_child().get_first_child()

    def _set_highlight(self, widget: Gtk.Widget, kind: str):
        if widget is self._hl_widget and kind == self._hl_kind:
            return
        self._clear_highlight()
        widget.add_css_class(_HL_CLASSES[kind])
        self._hl_widget, self._hl_kind = widget, kind
        if kind == "group":  # l'icône annonce la création d'un dossier
            icon = self._row_icon(widget)
            self._hl_icon = icon.get_icon_name()
            icon.set_from_icon_name("folder-new-symbolic")

    def _clear_highlight(self):
        widget = self._hl_widget
        if widget is None:
            return
        widget.remove_css_class(_HL_CLASSES[self._hl_kind])
        if self._hl_kind == "group" and self._hl_icon:
            self._row_icon(widget).set_from_icon_name(self._hl_icon)
        self._hl_widget = self._hl_kind = self._hl_icon = None

    # --- ouverture automatique d'un dossier survolé

    def _schedule_spring(self, path: Path):
        if self._spring_path == path:
            return
        self._cancel_spring()
        self._spring_path = path
        self._spring_source = GLib.timeout_add(SPRING_DELAY_MS, self._spring_fire, path)

    def _cancel_spring(self):
        if self._spring_source:
            GLib.source_remove(self._spring_source)
        self._spring_source = 0
        self._spring_path = None

    def _spring_fire(self, path: Path):
        self._spring_source = 0
        self._spring_path = None
        _i, row = self._find_row(path)
        if row is not None:
            row.set_expanded(True)
        return GLib.SOURCE_REMOVE

    # --- exécution

    def _perform_drop(self, kind: str, src: Path, target: Path | None):
        self._flush()  # la note ouverte est enregistrée avant que son fichier ne bouge
        snapshot = self._expanded_snapshot()  # dossiers ouverts, pour suivre un dossier déplacé
        src_name = src.name if src.is_dir() else src.stem
        try:
            if kind == "group":
                folder, new_src, new_target = self.store.group_notes(src, target)
                self._path_moved(src, new_src)
                self._path_moved(target, new_target)
                select, expand = folder, {folder}
                message = _("Dossier « {name} » créé").format(name=folder.name)
            else:
                dest = self.store.root if kind == "root" else target
                new = self.store.move(src, dest)
                self._path_moved(src, new)
                select, expand = new, self._remap_paths(snapshot, src, new)
                if dest == self.store.root:
                    message = _("« {name} » déplacé à la racine").format(name=src_name)
                else:
                    message = _("« {name} » déplacé dans « {folder} »").format(
                        name=src_name, folder=dest.name)
        except StorageError as e:
            self.toast(str(e))
            return GLib.SOURCE_REMOVE

        self.refresh_tree(select_path=select, expand_paths=expand)
        self.toast(message)
        return GLib.SOURCE_REMOVE

    def _perform_import(self, files: list, dest: Path):
        """Copie les fichiers externes déposés (Gio.File) dans `dest`."""
        imported: list[Path] = []
        error = None
        for file in files:
            path = file.get_path()  # None pour un fichier distant (sftp://…)
            try:
                if path is None:
                    raise StorageError(_("Seuls les fichiers .md et .txt peuvent être importés."))
                imported.append(self.store.import_note(Path(path), dest))
            except StorageError as e:
                error = str(e)

        if imported:
            self.refresh_tree(select_path=imported[-1])
            if len(imported) == 1:
                self.open_note(imported[0])
                self.toast(_("« {name} » importé").format(name=imported[0].stem))
            else:
                self.toast(_("{count} notes importées").format(count=len(imported)))
        if error is not None:  # au moins un fichier refusé (dossier, image…)
            self.toast(error)
        return GLib.SOURCE_REMOVE
