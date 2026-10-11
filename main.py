#!/usr/bin/env python3
"""
Lumina — prise de notes ultra-minimaliste (GTK4 + Libadwaita).

Ce fichier ne contient que l'interface graphique. Toute la logique de fichiers
(parcours, création, déplacement, regroupement, écriture atomique, filtrage)
est dans storage.py.

Glisser-déposer dans la barre latérale :
  * note  -> note     : crée un dossier parent (nommé d'après la cible) et y range les deux ;
  * note  -> dossier  : déplace la note dans le dossier ;
  * dossier -> dossier: déplace le dossier (les cycles sont refusés) ;
  * élément -> en-tête de la sidebar ou zone vide sous la liste : remonte à la racine.
Un dossier replié s'ouvre tout seul si on le survole quelques instants.

Dépendances :
  Debian/Ubuntu : sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
  Fedora        : sudo dnf install python3-gobject gtk4 libadwaita
  Windows       : MSYS2 UCRT64 -> pacman -S mingw-w64-ucrt-x86_64-{gtk4,libadwaita,python-gobject}
"""

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from storage import Node, NoteStore, StorageError, filter_tree  # noqa: E402

APP_ID = "dev.lumina.Notes"
SAVE_DELAY_MS = 1000     # debouncing de la sauvegarde automatique
SPRING_DELAY_MS = 700    # survol d'un dossier replié pendant un glissement -> il s'ouvre

CSS = """
textview.lumina-editor,
textview.lumina-editor text {
    background: transparent;
    font-size: 115%;
}

/* ---- Glisser-déposer : retours visuels (couleurs d'accent Libadwaita) ---- */
.lumina-row {
    border-radius: 8px;
    outline: 2px solid transparent;
    outline-offset: -2px;
    transition: background-color 150ms ease-out,
                outline-color 150ms ease-out,
                opacity 150ms ease-out;
}
.lumina-row.lumina-dragging {            /* l'élément en cours de déplacement */
    opacity: 0.4;
}
.lumina-row.lumina-drop-into {           /* dépôt DANS un dossier */
    background-color: alpha(@accent_bg_color, 0.18);
    outline-color: alpha(@accent_bg_color, 0.9);
}
.lumina-row.lumina-drop-group {          /* dépôt sur une note : création d'un dossier */
    background-color: alpha(@accent_bg_color, 0.32);
    outline-color: @accent_color;
}
.lumina-root-zone {
    transition: background-color 150ms ease-out, box-shadow 150ms ease-out;
}
.lumina-root-zone.lumina-drop-root {     /* dépôt à la racine */
    background-color: alpha(@accent_bg_color, 0.14);
    box-shadow: inset 0 0 0 2px alpha(@accent_bg_color, 0.8);
}
"""

# Type de dépôt -> classe CSS appliquée à la cible survolée
_HL_CLASSES = {
    "move": "lumina-drop-into",
    "group": "lumina-drop-group",
    "root": "lumina-drop-root",
}


class NoteItem(GObject.Object):
    """Enveloppe GObject autour d'un `Node` (requis par Gio.ListStore)."""

    __gtype_name__ = "LuminaNoteItem"

    def __init__(self, node: Node):
        super().__init__()
        self.node = node
        # Sous-modèle du dossier, créé à la demande puis conservé : on peut ainsi
        # le modifier en place (insertions / suppressions) sans recharger l'arbre.
        self.children_store: Gio.ListStore | None = None


class LuminaWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, store: NoteStore):
        super().__init__(application=app, title="Lumina",
                         default_width=1100, default_height=720)
        self.store = store

        # État de l'éditeur
        self.current_path: Path | None = None
        self._loading = False       # vrai pendant le chargement d'une note
        self._dirty = False         # modifications non enregistrées
        self._save_source = 0       # id du timeout de sauvegarde en attente

        # État de l'arbre
        self.tree_model: Gtk.TreeListModel | None = None
        self.root_store: Gio.ListStore | None = None
        self._model_filtered = False
        self._expanded: set[Path] = set()
        self._query = ""

        # État du glisser-déposer
        self._drag_src: Path | None = None   # élément en cours de glissement
        self._drag_src_is_dir = False
        self._drag_widget: Gtk.Widget | None = None
        self._hl_widget: Gtk.Widget | None = None   # cible actuellement en surbrillance
        self._hl_kind: str | None = None
        self._hl_icon: str | None = None
        self._spring_source = 0
        self._spring_path: Path | None = None

        self._build_ui()
        self._build_actions()
        self.reload_tree()
        self.connect("close-request", self._on_close_request)

    # ================================================================== UI

    def _build_ui(self):
        self.split_view = Adw.OverlaySplitView(
            min_sidebar_width=240, max_sidebar_width=340, sidebar_width_fraction=0.26)
        self.split_view.set_sidebar(self._build_sidebar())
        self.split_view.set_content(self._build_content())
        self.set_content(self.split_view)

        # Sur fenêtre étroite, la barre latérale passe en superposition.
        try:
            bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
            bp.add_setter(self.split_view, "collapsed", True)
            self.add_breakpoint(bp)
        except (AttributeError, TypeError):  # libadwaita < 1.4
            pass

    def _build_sidebar(self) -> Adw.ToolbarView:
        header = Adw.HeaderBar(show_end_title_buttons=False)
        header.set_title_widget(Adw.WindowTitle(title="Lumina", subtitle=""))

        menu = Gio.Menu()
        menu.append("Nouvelle note", "win.new-note")
        menu.append("Nouveau dossier", "win.new-folder")
        new_button = Gtk.MenuButton(icon_name="list-add-symbolic", menu_model=menu,
                                    tooltip_text="Nouveau")
        header.pack_start(new_button)

        self.search_entry = Gtk.SearchEntry(placeholder_text="Rechercher…",
                                            margin_start=12, margin_end=12,
                                            margin_top=6, margin_bottom=6)
        self.search_entry.connect("search-changed", self._on_search_changed)

        # Fabrique des lignes : TreeExpander > Box[icône, libellé]
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)
        factory.connect("unbind", self._on_factory_unbind)

        self.selection = Gtk.SingleSelection(autoselect=False, can_unselect=True)
        self.list_view = Gtk.ListView(model=self.selection, factory=factory,
                                      single_click_activate=True)
        self.list_view.add_css_class("navigation-sidebar")
        self.list_view.connect("activate", self._on_row_activated)

        scrolled = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      child=self.list_view)

        # Zones « racine » : en-tête (toujours visible) + espace vide sous la liste.
        # (Pas sur le champ de recherche : il accepterait le texte déposé.)
        self._add_root_drop_target(header)
        self._add_root_drop_target(scrolled)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.search_entry)
        box.append(scrolled)

        view = Adw.ToolbarView(content=box)
        view.add_top_bar(header)
        return view

    def _build_content(self) -> Adw.ToolbarView:
        # En-tête : bouton de bascule de la sidebar + titre de la note au centre.
        header = Adw.HeaderBar(show_start_title_buttons=False)
        toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic",
                                  tooltip_text="Afficher / masquer la barre latérale")
        self.split_view.bind_property(
            "show-sidebar", toggle, "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        header.pack_start(toggle)
        self.window_title = Adw.WindowTitle(title="Lumina", subtitle="")
        header.set_title_widget(self.window_title)

        # Page « aucune note »
        empty_button = Gtk.Button(label="Nouvelle note", halign=Gtk.Align.CENTER,
                                  action_name="win.new-note")
        empty_button.add_css_class("suggested-action")
        empty_button.add_css_class("pill")
        empty = Adw.StatusPage(icon_name="document-edit-symbolic",
                               title="Aucune note ouverte",
                               description="Sélectionnez une note dans la barre latérale "
                                           "ou créez-en une nouvelle.",
                               child=empty_button)

        # Page éditeur : TextView centré, sans bordure, avec de larges marges.
        self.buffer = Gtk.TextBuffer()
        self.buffer.connect("changed", self._on_buffer_changed)
        self.text_view = Gtk.TextView(buffer=self.buffer, vexpand=True,
                                      wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                      top_margin=40, bottom_margin=40,
                                      left_margin=40, right_margin=40,
                                      pixels_inside_wrap=4, pixels_below_lines=2)
        self.text_view.add_css_class("lumina-editor")
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=600,
                          vexpand=True, child=self.text_view)
        editor = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(empty, "empty")
        self.stack.add_named(editor, "editor")
        self.stack.set_visible_child_name("empty")

        self.toast_overlay = Adw.ToastOverlay(child=self.stack)
        view = Adw.ToolbarView(content=self.toast_overlay)
        view.add_top_bar(header)
        return view

    def _build_actions(self):
        for name, callback in (("new-note", lambda *_: self._create_entry(False)),
                               ("new-folder", lambda *_: self._create_entry(True)),
                               ("toggle-sidebar", self._on_toggle_sidebar)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def toast(self, message: str):
        self.toast_overlay.add_toast(Adw.Toast.new(message))

    # ================================================================== Arbre

    def reload_tree(self, select_path: Path | None = None,
                    expand_paths: set[Path] | None = None):
        """
        Reconstruit entièrement le modèle (changement de filtre de recherche).
        Pour les modifications du disque, préférer `refresh_tree`, plus fluide.
        """
        # Mémorise les dossiers ouverts (uniquement hors recherche : en recherche
        # tout est déplié automatiquement et ne doit pas polluer l'état).
        if self.tree_model is not None and not self._model_filtered:
            self._expanded = self._collect_expanded()

        root = self.store.scan()
        filtered = bool(self._query)
        if filtered:
            root = filter_tree(root, self._query)

        self.root_store = Gio.ListStore(item_type=NoteItem)
        for child in root.children:
            self.root_store.append(NoteItem(child))

        # TreeListModel : chaque dossier fournit un sous-modèle via `_create_children`.
        self.tree_model = Gtk.TreeListModel.new(self.root_store, False, filtered,
                                                self._create_children)
        self._model_filtered = filtered
        self.selection.set_model(self.tree_model)

        if not filtered:
            wanted = set(self._expanded) | set(expand_paths or ())
            if select_path is not None:
                wanted.update(p for p in select_path.parents if p != self.store.root)
            self._expanded = wanted
            self._expand_rows(wanted)

        target = select_path or self.current_path
        if target is not None:
            self._select_path(target)

    def refresh_tree(self, select_path: Path | None = None,
                     expand_paths: set[Path] | None = None):
        """
        Met l'arbre à jour d'après le disque SANS remplacer le modèle : seules les
        lignes ajoutées / supprimées changent. L'état d'expansion, la sélection et
        la position de défilement sont conservés.
        """
        if self.tree_model is None or self._model_filtered or self._query:
            self.reload_tree(select_path, expand_paths)  # vue filtrée : reconstruction
            return

        self._sync_store(self.root_store, self.store.scan().children)

        wanted = set(expand_paths or ())
        if select_path is not None:
            wanted.update(p for p in select_path.parents if p != self.store.root)
        if wanted:
            self._expand_rows(wanted)
        if select_path is not None:
            self._select_path(select_path)

    def _sync_store(self, store: Gio.ListStore, nodes: list[Node]):
        """
        Aligne `store` sur `nodes` en supprimant / insérant uniquement la différence.
        Les éléments conservés gardent leur sous-modèle (donc leur état déplié) ;
        la synchronisation descend dans les sous-modèles déjà créés.
        """
        wanted = {n.path for n in nodes}

        # 1. Retire ce qui a disparu.
        i = 0
        while i < store.get_n_items():
            if store.get_item(i).node.path in wanted:
                i += 1
            else:
                store.remove(i)

        # 2. Met à jour l'existant et insère le nouveau, à la bonne position.
        present = {}
        for i in range(store.get_n_items()):
            item = store.get_item(i)
            present[item.node.path] = item
        for pos, node in enumerate(nodes):
            item = present.get(node.path)
            if item is None:
                store.insert(min(pos, store.get_n_items()), NoteItem(node))
            else:
                item.node = node
                if item.children_store is not None:
                    self._sync_store(item.children_store, node.children)

    @staticmethod
    def _create_children(item: NoteItem):
        if not item.node.is_dir:
            return None
        if item.children_store is None:
            item.children_store = Gio.ListStore(item_type=NoteItem)
            for child in item.node.children:
                item.children_store.append(NoteItem(child))
        return item.children_store

    def _collect_expanded(self) -> set[Path]:
        expanded = set()
        for i in range(self.tree_model.get_n_items()):
            row = self.tree_model.get_row(i)
            if row.get_expanded():
                expanded.add(row.get_item().node.path)
        return expanded

    def _expand_rows(self, paths: set[Path]):
        # Déplier une ligne insère ses enfants juste après : la boucle les visite aussi.
        i = 0
        while i < self.tree_model.get_n_items():
            row = self.tree_model.get_row(i)
            node = row.get_item().node
            if node.is_dir and node.path in paths:
                row.set_expanded(True)
            i += 1

    def _find_row(self, path: Path):
        """Retourne (position, TreeListRow) de `path` dans le modèle, ou (None, None)."""
        for i in range(self.tree_model.get_n_items()):
            row = self.tree_model.get_row(i)
            if row.get_item().node.path == path:
                return i, row
        return None, None

    def _select_path(self, path: Path):
        i, _row = self._find_row(path)
        if i is None:
            return
        self.selection.set_selected(i)
        try:
            self.list_view.scroll_to(i, Gtk.ListScrollFlags.NONE, None)
        except (AttributeError, TypeError):  # GTK < 4.12
            pass

    # --- fabrique de lignes

    def _on_factory_setup(self, _factory, list_item):
        box = Gtk.Box(spacing=8)
        box.append(Gtk.Image())
        box.append(Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        expander = Gtk.TreeExpander(child=box)
        expander.add_css_class("lumina-row")
        list_item.set_child(expander)

        # Chaque ligne est à la fois une SOURCE de glissement et une CIBLE de dépôt.
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare, list_item)
        source.connect("drag-begin", self._on_drag_begin, list_item)
        source.connect("drag-end", self._on_drag_end)
        expander.add_controller(source)

        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("enter", self._on_row_drop_motion, list_item)
        target.connect("motion", self._on_row_drop_motion, list_item)
        target.connect("leave", self._on_row_drop_leave, list_item)
        target.connect("drop", self._on_row_drop, list_item)
        expander.add_controller(target)

    def _on_factory_bind(self, _factory, list_item):
        row = list_item.get_item()  # Gtk.TreeListRow
        node = row.get_item().node
        expander = list_item.get_child()
        expander.set_list_row(row)
        icon = expander.get_child().get_first_child()
        label = icon.get_next_sibling()
        icon.set_from_icon_name("folder-symbolic" if node.is_dir else "text-x-generic-symbolic")
        label.set_text(node.title)

    def _on_factory_unbind(self, _factory, list_item):
        expander = list_item.get_child()
        if expander is self._hl_widget:  # la ligne est recyclée : on nettoie ses effets
            self._clear_highlight()
        expander.remove_css_class("lumina-dragging")
        expander.set_list_row(None)

    # --- interactions

    def _on_row_activated(self, _list_view, position):
        row = self.selection.get_item(position)
        node = row.get_item().node
        if node.is_dir:
            row.set_expanded(not row.get_expanded())
        else:
            self.open_note(node.path)

    def _on_search_changed(self, entry: Gtk.SearchEntry):
        self._query = entry.get_text().strip()
        self.reload_tree()

    def _on_toggle_sidebar(self, *_):
        self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())

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

    def _on_row_drop_motion(self, _target, _x, _y, list_item):
        row = list_item.get_item()
        if row is None:
            return 0
        node = row.get_item().node
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
        src = self._drag_src
        if row is None or src is None or str(value) != str(src):
            return False
        node = row.get_item().node
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
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("enter", self._on_root_drop_motion)
        target.connect("motion", self._on_root_drop_motion)
        target.connect("leave", self._on_root_drop_leave)
        target.connect("drop", self._on_root_drop)
        widget.add_controller(target)

    def _on_root_drop_motion(self, target, _x, _y):
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
        src_name = src.name if src.is_dir() else src.stem
        try:
            if kind == "group":
                folder, new_src, new_target = self.store.group_notes(src, target)
                self._remap_current(src, new_src)
                self._remap_current(target, new_target)
                select, expand = folder, {folder}
                message = f"Dossier « {folder.name} » créé"
            else:
                dest = self.store.root if kind == "root" else target
                new = self.store.move(src, dest)
                self._remap_current(src, new)
                select, expand = new, set()
                where = "à la racine" if dest == self.store.root else f"dans « {dest.name} »"
                message = f"« {src_name} » déplacé {where}"
        except StorageError as e:
            self.toast(str(e))
            return GLib.SOURCE_REMOVE

        if self.current_path is not None:
            self.window_title.set_title(self.current_path.stem)
        self.refresh_tree(select_path=select, expand_paths=expand)
        self.toast(message)
        return GLib.SOURCE_REMOVE

    def _remap_current(self, old: Path, new: Path):
        """Si la note ouverte se trouvait dans `old`, suit son déplacement vers `new`."""
        if self.current_path is None:
            return
        try:
            relative = self.current_path.relative_to(old)
        except ValueError:
            return
        self.current_path = new / relative

    # ================================================================== Création

    def _target_folder(self) -> Path:
        """Dossier cible : la sélection si c'est un dossier, sinon son parent."""
        row = self.selection.get_selected_item()
        if row is None:
            return self.store.root
        node = row.get_item().node
        return node.path if node.is_dir else node.path.parent

    def _create_entry(self, is_folder: bool):
        parent = self._target_folder()

        def on_name(name: str):
            try:
                if is_folder:
                    path = self.store.create_folder(parent, name)
                else:
                    path = self.store.create_note(parent, name)
            except StorageError as e:
                self.toast(str(e))
                return
            self.search_entry.set_text("")  # efface un éventuel filtre
            self._query = ""
            self.refresh_tree(select_path=path)
            if not is_folder:
                self.open_note(path)

        self._ask_name(
            heading="Nouveau dossier" if is_folder else "Nouvelle note",
            body=f"Créé dans : {parent.relative_to(self.store.root)}"
            if parent != self.store.root else "Créé à la racine",
            default="Nouveau dossier" if is_folder else "Sans titre",
            ok_label="Créer",
            on_name=on_name)

    def _ask_name(self, heading, body, default, ok_label, on_name):
        """Petite boîte de dialogue avec un champ de saisie (AlertDialog si dispo)."""
        entry = Gtk.Entry(text=default, activates_default=True)
        entry.connect("map", lambda w: w.grab_focus())

        if hasattr(Adw, "AlertDialog"):  # libadwaita >= 1.5
            dialog = Adw.AlertDialog(heading=heading, body=body)
            show = lambda: dialog.present(self)  # noqa: E731
        else:
            dialog = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
            show = dialog.present

        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Annuler")
        dialog.add_response("ok", ok_label)
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response == "ok":
                on_name(entry.get_text())

        dialog.connect("response", on_response)
        show()

    # ================================================================== Éditeur

    def open_note(self, path: Path):
        self._flush()  # enregistre la note précédente avant d'en changer
        try:
            text = self.store.read_note(path)
        except StorageError as e:
            self.toast(str(e))
            return

        self._loading = True
        self.buffer.begin_irreversible_action()  # Ctrl+Z ne doit pas « décharger » la note
        self.buffer.set_text(text)
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self.buffer.end_irreversible_action()
        self._loading = False
        self._dirty = False

        self.current_path = path
        self.window_title.set_title(path.stem)
        self.window_title.set_subtitle("")
        self.stack.set_visible_child_name("editor")
        self.text_view.grab_focus()

        if self.split_view.get_collapsed():  # mode étroit : on révèle l'éditeur
            self.split_view.set_show_sidebar(False)

    # --- sauvegarde automatique (debouncing)

    def _on_buffer_changed(self, _buffer):
        if self._loading or self.current_path is None:
            return
        self._dirty = True
        self.window_title.set_subtitle("Modifications en cours…")
        # Chaque frappe relance le minuteur : on n'écrit qu'après 1 s d'inactivité.
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self._on_save_timeout)

    def _on_save_timeout(self):
        self._save_source = 0
        self._flush()
        return GLib.SOURCE_REMOVE

    def _flush(self):
        """Enregistre immédiatement si nécessaire (changement de note, fermeture…)."""
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        if not self._dirty or self.current_path is None:
            return
        text = self.buffer.get_text(self.buffer.get_start_iter(),
                                    self.buffer.get_end_iter(), False)
        try:
            self.store.write_note(self.current_path, text)
        except StorageError as e:
            self.toast(str(e))
            return
        self._dirty = False
        self.window_title.set_subtitle("Enregistré")

    def _on_close_request(self, _window):
        self._flush()
        return False  # laisse la fenêtre se fermer


class LuminaApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        if hasattr(provider, "load_from_string"):  # GTK >= 4.12
            provider.load_from_string(CSS)
        else:
            provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.set_accels_for_action("win.new-note", ["<Primary>n"])
        self.set_accels_for_action("win.new-folder", ["<Primary><Shift>n"])
        self.set_accels_for_action("win.toggle-sidebar", ["<Primary>b", "F9"])

    def do_activate(self):
        win = self.props.active_window
        if win is None:
            win = LuminaWindow(self, NoteStore())
        win.present()


def main() -> int:
    return LuminaApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())