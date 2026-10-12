"""
sidebar.py — barre latérale de Lumina.

  en-tête      « + » (nouvelle note, nouvel onglet, nouveau dossier) et menu principal
  recherche    titres ET contenu des notes, avec un extrait sous chaque résultat
  Épinglées    pinned.py
  arborescence Gtk.ListView + Gtk.TreeListModel (ci-dessous)
  Corbeille    trash_view.py, à la place de l'arborescence
  pied         bascule Notes / Corbeille

Principe : la barre latérale AFFICHE et SIGNALE, elle ne modifie jamais le
disque. Les commandes passent par les actions « win.* » de la fenêtre (menus,
touche Suppr) ou par les rappels de `SidebarCallbacks` (ouverture, glisser-
déposer) ; une fois l'opération faite, la fenêtre appelle `refresh()`.

Arborescence : chaque dossier a son propre Gio.ListStore, créé à son premier
dépliage puis conservé. `refresh()` relit le disque et n'applique que les
DIFFÉRENCES : dossiers ouverts, sélection et défilement survivent ainsi à
chaque modification. Pendant une recherche, le modèle est reconstruit (filtré).

Menus contextuels (clic droit, appui long) — voir context_menu.py :
  zone vide · dossier · note · note épinglée · élément de la corbeille.

Glisser-déposer :
  note -> note       regroupe les deux dans un nouveau dossier
  élément -> dossier déplace dedans (un dossier replié survolé s'ouvre)
  élément -> en-tête de la barre latérale : remonte à la racine
  note -> éditeur    ouvre la note dans un nouvel onglet (`attach_open_target`)
  fichiers externes  (Nautilus…) copiés dans le dossier survolé
Un glissement interne transporte un `DragPayload` (type propre à Lumina, que le
TextView ou le champ de recherche ignorent), un glissement externe une Gdk.FileList.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from context_menu import (build_empty_menu, build_item_menu, build_trash_menu,  # noqa: E402
                          popup_menu)
from i18n import _  # noqa: E402
from pinned import PinnedSection  # noqa: E402
from pins import PinStore  # noqa: E402
from storage import Node, Snippet, StorageManager, TrashEntry, filter_tree, iter_notes  # noqa: E402
from trash_view import TrashView  # noqa: E402

TRANSITION_MS = 220      # fondu Notes / Corbeille
SPRING_DELAY_MS = 700    # survol d'un dossier replié pendant un glissement -> il s'ouvre

# Type de dépôt -> classe CSS de la cible survolée (voir styles.py)
_HIGHLIGHT_CLASSES = {
    "move": "lumina-drop-into",
    "group": "lumina-drop-group",
    "root": "lumina-drop-root",
    "open": "lumina-drop-open",
}


@dataclass
class SidebarCallbacks:
    """Ce que la barre latérale demande à la fenêtre."""

    open_note: Callable[[Path, bool], None]          # (note, dans un nouvel onglet ?)
    before_search: Callable[[], None]                # enregistre les onglets avant de lire le disque
    leave_search: Callable[[], None]                 # Échap dans la recherche
    move: Callable[[Path, Path], None]               # (élément, dossier de destination)
    group: Callable[[Path, Path], None]              # (note glissée, note cible)
    import_files: Callable[[list[Path], Path], None]  # (fichiers externes, dossier)
    trash_changed: Callable[[bool], None]            # la corbeille est-elle non vide ?


class NoteItem(GObject.Object):
    """Enveloppe GObject autour d'un `Node` (requis par Gio.ListStore)."""

    __gtype_name__ = "LuminaNoteItem"

    def __init__(self, node: Node):
        super().__init__()
        self.node = node
        # Sous-modèle du dossier, créé au premier dépliage puis conservé.
        self.children_store: Gio.ListStore | None = None


class DragPayload(GObject.Object):
    """Charge utile d'un glissement interne : un type propre à Lumina, pas du texte."""

    __gtype_name__ = "LuminaDragPayload"

    # Propriété GObject (et non simple attribut Python) : stockée côté GObject, elle
    # traverse sans perte le transport GValue (Gdk.ContentProvider -> Gtk.DropTarget).
    path = GObject.Property(type=str, default="")

    def __init__(self, path: Path):
        super().__init__(path=str(path))


def _highlight(text: str, start: int, end: int) -> str:
    """Balisage Pango de `text` avec text[start:end] en gras (occurrence recherchée)."""
    esc = GLib.markup_escape_text
    return f"{esc(text[:start])}<b>{esc(text[start:end])}</b>{esc(text[end:])}"


class Sidebar(Adw.Bin):   # Adw.ToolbarView est une classe finale : on l'enveloppe
    __gtype_name__ = "LuminaSidebar"

    def __init__(self, store: StorageManager, pins: PinStore, callbacks: SidebarCallbacks):
        super().__init__()
        self.store = store
        self.pins = pins
        self.cb = callbacks

        # Arborescence
        self._root_store = Gio.ListStore(item_type=NoteItem)
        self._tree_model: Gtk.TreeListModel | None = None
        self._filtered = False                       # le modèle affiché est-il filtré ?
        self._expanded: set[Path] = set()            # dossiers ouverts (hors recherche)
        self._query = ""
        self._scan: Node | None = None               # dernier parcours du disque
        self._snippets: dict[Path, Snippet] = {}     # recherche : extrait par note trouvée
        self._current: Path | None = None            # note de l'onglet actif

        # Glisser-déposer
        self._drag_src: Path | None = None
        self._drag_src_is_dir = False
        self._drag_widget: Gtk.Widget | None = None
        self._hl_widget: Gtk.Widget | None = None    # cible actuellement en surbrillance
        self._hl_kind: str | None = None
        self._hl_icon: str | None = None
        self._spring_source = 0
        self._spring_path: Path | None = None

        self._build()
        self._rebuild(rescan=True)

    # ================================================================== construction

    def _build(self):
        header = Adw.HeaderBar(show_end_title_buttons=False)
        self.title = Adw.WindowTitle(title="Lumina", subtitle="")
        header.set_title_widget(self.title)

        new_menu = Gio.Menu()
        new_menu.append(_("Nouvelle note"), "win.new-note")
        new_menu.append(_("Nouvel onglet"), "win.new-tab")
        new_menu.append(_("Nouveau dossier"), "win.new-folder")
        # Le bouton « + » est collé au bord gauche de la fenêtre : centré sur lui (le
        # défaut), le menu — large, avec ses raccourcis — déborderait à gauche. Il est
        # donc ancré sous le bouton et aligné sur son bord gauche : il s'ouvre vers
        # l'intérieur de la fenêtre.
        new_popover = Gtk.PopoverMenu.new_from_model(new_menu)
        new_button = Gtk.MenuButton(icon_name="list-add-symbolic", popover=new_popover,
                                    direction=Gtk.ArrowType.DOWN, tooltip_text=_("Nouveau"))
        new_popover.set_position(Gtk.PositionType.BOTTOM)
        new_popover.set_halign(Gtk.Align.START)
        header.pack_start(new_button)

        # Menu principal. La Corbeille n'y figure pas : son bouton à bascule, en bas
        # de la barre latérale, est le seul accès (pas de doublon).
        main_menu = Gio.Menu()
        for label, action in ((_("Imprimer / Exporter en PDF…"), "win.print"),
                              (_("Préférences"), "app.preferences")):
            section = Gio.Menu()
            section.append(label, action)
            main_menu.append_section(None, section)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=main_menu,
                                       tooltip_text=_("Menu principal"), primary=True))

        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Rechercher…"), margin_start=12,
                                            margin_end=12, margin_top=6, margin_bottom=6)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("stop-search", self._on_stop_search)   # Échap

        # Épinglées (masquées tant qu'il n'y en a aucune)
        self.pinned = PinnedSection(self.store.root,
                                    on_activate=lambda path: self.cb.open_note(path, False),
                                    on_context=self._on_pinned_context)
        self._add_delete_key(self.pinned.list_box, self.pinned.focused_path)
        separator = Gtk.Separator(margin_top=4, margin_bottom=4)
        self.pinned.bind_property("reveal-child", separator, "visible",
                                  GObject.BindingFlags.SYNC_CREATE)

        # Arborescence
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)
        factory.connect("unbind", self._on_factory_unbind)
        self.selection = Gtk.SingleSelection(autoselect=False, can_unselect=True)
        self.list_view = Gtk.ListView(model=self.selection, factory=factory,
                                      single_click_activate=True)
        self.list_view.add_css_class("navigation-sidebar")
        self.list_view.connect("activate", self._on_row_activated)
        self._add_delete_key(self.list_view, self._selected_path)
        scrolled = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      child=self.list_view)

        # Cibles de dépôt : UNIQUEMENT les lignes (voir _attach_row_dnd) et l'en-tête
        # (remonter à la racine). Surtout pas la zone défilante : elle recevait les
        # dépôts refusés par une ligne, s'allumait en entier et déplaçait l'élément
        # à la racine.
        self._add_root_drop_target(header)
        # Menu de la zone vide. Un clic droit sur une ligne est réclamé par la ligne.
        self._attach_menu(scrolled, lambda: build_empty_menu(self.store.root))

        notes_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for child in (self.search_entry, self.pinned, separator, scrolled):
            notes_page.append(child)

        self.trash_view = TrashView(self.store.root, on_context=self._on_trash_context)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=TRANSITION_MS)
        self.stack.add_named(notes_page, "notes")
        self.stack.add_named(self.trash_view, "trash")

        trash_toggle = Gtk.ToggleButton(action_name="win.show-trash",
                                        child=Adw.ButtonContent(icon_name="user-trash-symbolic",
                                                                label=_("Corbeille")))
        trash_toggle.add_css_class("flat")
        bottom = Gtk.Box(margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        bottom.append(trash_toggle)

        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        view.add_bottom_bar(bottom)
        self.set_child(view)

    # ================================================================== API

    def refresh(self, select: Path | None = None, expand: set[Path] | None = None) -> None:
        """
        Resynchronise l'affichage avec le disque (après toute opération, ou des
        changements faits hors de Lumina). `select` est sélectionné (ses dossiers
        parents dépliés) ; `expand` : dossiers à déplier en plus.
        """
        if self._filtered or self._query:
            self._rebuild(rescan=True)
        else:
            self._scan = self.store.scan()
            self._sync(self._root_store, self._scan.children)
            wanted = set(expand or ())
            if select is not None:
                wanted |= self._ancestors(select)
            self._expand(wanted)
        if select is not None:
            self.select(select)
        self.refresh_pins()
        self.refresh_trash()

    def set_current(self, path: Path | None) -> None:
        """Note de l'onglet actif : mise en évidence dans l'arbre et les épingles."""
        self._current = path
        self.pinned.set_current(path)
        if path is None:
            self.selection.unselect_all()
        else:
            self._expand(self._ancestors(path))
            self.select(path)

    def select(self, path: Path) -> None:
        position, _row = self._find_row(path)
        if position is None:
            return
        self.selection.set_selected(position)
        try:
            self.list_view.scroll_to(position, Gtk.ListScrollFlags.NONE, None)
        except (AttributeError, TypeError):   # GTK < 4.12
            pass

    def focused_path(self) -> Path | None:
        """Élément ayant le focus clavier dans la barre latérale (F2, Ctrl+N…), sinon None."""
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        if focus is None:
            return None
        if focus is self.pinned or focus.is_ancestor(self.pinned):
            return self.pinned.focused_path()
        if focus is self.list_view or focus.is_ancestor(self.list_view):
            return self._selected_path()
        return None

    def expanded_paths(self) -> set[Path]:
        """Dossiers ouverts (pour suivre un dossier renommé / déplacé)."""
        return set() if self._filtered else self._collect_expanded()

    def refresh_pins(self) -> None:
        self.pins.prune()   # oublie les notes supprimées hors de Lumina
        self.pinned.set_pins(self.pins.paths())
        self.pinned.set_enabled(not self._query)
        self.pinned.set_current(self._current)

    def refresh_trash(self) -> None:
        entries = self.store.list_trash()
        self.trash_view.set_entries(entries)
        self.cb.trash_changed(bool(entries))

    @property
    def trash_shown(self) -> bool:
        return self.stack.get_visible_child_name() == "trash"

    def show_trash(self, show: bool) -> None:
        self.stack.set_visible_child_name("trash" if show else "notes")
        self.title.set_title(_("Corbeille") if show else "Lumina")
        if show:
            self.refresh_trash()

    def focus_search(self) -> None:
        self.show_trash(False)
        GLib.idle_add(lambda: (self.search_entry.grab_focus(), GLib.SOURCE_REMOVE)[1])

    def clear_search(self) -> None:
        """Vide la recherche (un nouvel élément doit être visible)."""
        if self._query:
            self._query = ""
            self.search_entry.set_text("")
            self._rebuild(rescan=True)

    # ================================================================== modèle

    def _rebuild(self, rescan: bool) -> None:
        """Reconstruit entièrement le modèle (recherche) ; `rescan` relit le disque."""
        if self._tree_model is not None and not self._filtered:
            self._expanded = self._collect_expanded()
        if rescan or self._scan is None:
            self._scan = self.store.scan()
        root = self._scan
        self._filtered = bool(self._query)
        if self._filtered:
            self._snippets = self.store.search_contents(iter_notes(root), self._query)
            root = filter_tree(root, self._query, self._snippets)
        else:
            self._snippets = {}

        self._root_store = Gio.ListStore(item_type=NoteItem)
        for child in root.children:
            self._root_store.append(NoteItem(child))
        # En recherche, tout est déplié (autoexpand) ; sinon on rétablit les dossiers ouverts.
        self._tree_model = Gtk.TreeListModel.new(self._root_store, False, self._filtered,
                                                 self._create_children)
        self.selection.set_model(self._tree_model)
        if not self._filtered:
            self._expand(self._expanded)
        if self._current is not None:
            self.select(self._current)
        self.refresh_pins()

    def _sync(self, store: Gio.ListStore, nodes: list[Node]) -> None:
        """
        Aligne `store` sur `nodes` en ne retirant / n'insérant que la différence ;
        descend dans les sous-modèles déjà créés (dossiers déjà dépliés une fois).
        """
        wanted = {n.path: n for n in nodes}
        # 1. Retire ce qui a disparu ou dont l'affichage change (nom, même la seule
        #    casse : Path("a") == Path("A") sous Windows ; dossier devenu note…).
        i = 0
        while i < store.get_n_items():
            item = store.get_item(i)
            node = wanted.get(item.node.path)
            if node is not None and node.path.name == item.node.path.name \
                    and node.is_dir == item.node.is_dir:
                i += 1
            else:
                store.remove(i)
        # 2. Met à jour ce qui reste et insère le nouveau à sa place (même ordre que `nodes`).
        present = {store.get_item(i).node.path: store.get_item(i) for i in range(store.get_n_items())}
        for position, node in enumerate(nodes):
            item = present.get(node.path)
            if item is None:
                store.insert(min(position, store.get_n_items()), NoteItem(node))
            else:
                item.node = node
                if item.children_store is not None:
                    self._sync(item.children_store, node.children)

    @staticmethod
    def _create_children(item: NoteItem) -> Gio.ListStore | None:
        if not item.node.is_dir:
            return None
        if item.children_store is None:
            item.children_store = Gio.ListStore(item_type=NoteItem)
            for child in item.node.children:
                item.children_store.append(NoteItem(child))
        return item.children_store

    def _rows(self):
        """(position, Gtk.TreeListRow) de chaque ligne visible."""
        model = self._tree_model
        for i in range(model.get_n_items() if model is not None else 0):
            yield i, model.get_row(i)

    def _collect_expanded(self) -> set[Path]:
        return {row.get_item().node.path for _i, row in self._rows() if row.get_expanded()}

    def _expand(self, paths: set[Path]) -> None:
        if not paths or self._tree_model is None or self._filtered:
            return
        # Déplier une ligne insère ses enfants juste après : la boucle les visite aussi.
        i = 0
        while i < self._tree_model.get_n_items():
            row = self._tree_model.get_row(i)
            node = row.get_item().node
            if node.is_dir and node.path in paths and not row.get_expanded():
                row.set_expanded(True)
            i += 1

    def _ancestors(self, path: Path) -> set[Path]:
        """Dossiers entre la racine (exclue) et `path` (exclu)."""
        return {p for p in path.parents if self.store.root in p.parents}

    def _find_row(self, path: Path):
        for i, row in self._rows():
            if row.get_item().node.path == path:
                return i, row
        return None, None

    def _selected_path(self) -> Path | None:
        row = self.selection.get_selected_item()
        return row.get_item().node.path if row is not None else None

    # ================================================================== lignes

    def _on_factory_setup(self, _factory, list_item: Gtk.ListItem):
        # TreeExpander > Box[icône, Box[titre, extrait de recherche]]
        box = Gtk.Box(spacing=8)
        box.append(Gtk.Image())
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        texts.append(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))
        snippet = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, visible=False)
        snippet.add_css_class("caption")
        snippet.add_css_class("dim-label")
        texts.append(snippet)
        box.append(texts)
        expander = Gtk.TreeExpander(child=box)
        expander.add_css_class("lumina-row")
        list_item.set_child(expander)
        self._attach_row_menu(expander, list_item)
        self._attach_row_dnd(expander, list_item)

    def _on_factory_bind(self, _factory, list_item: Gtk.ListItem):
        row = list_item.get_item()
        node = row.get_item().node
        expander = list_item.get_child()
        expander.set_list_row(row)
        icon = expander.get_child().get_first_child()
        label = icon.get_next_sibling().get_first_child()
        snippet_label = label.get_next_sibling()
        icon.set_from_icon_name("folder-symbolic" if node.is_dir else "text-x-generic-symbolic")

        match = (re.search(re.escape(self._query), node.title, re.IGNORECASE)
                 if self._query else None)
        if match:
            label.set_markup(_highlight(node.title, match.start(), match.end()))
        else:
            label.set_text(node.title)
        snippet = self._snippets.get(node.path)
        if snippet is not None:
            snippet_label.set_markup(_highlight(snippet.text, snippet.start, snippet.end))
        snippet_label.set_visible(snippet is not None)

    def _on_factory_unbind(self, _factory, list_item: Gtk.ListItem):
        expander = list_item.get_child()
        if expander is self._hl_widget:   # ligne recyclée : on nettoie ses effets visuels
            self._clear_highlight()
        expander.remove_css_class("lumina-dragging")
        expander.set_list_row(None)

    def _on_row_activated(self, _list_view, position: int):
        row = self.selection.get_item(position)
        if row is None:
            return
        node = row.get_item().node
        if node.is_dir:
            row.set_expanded(not row.get_expanded())
        else:
            self.cb.open_note(node.path, False)

    # ================================================================== recherche

    def _on_search_changed(self, entry: Gtk.SearchEntry):
        query = entry.get_text().strip()
        if query == self._query:
            return
        self.cb.before_search()   # la recherche lit le disque : les onglets y sont d'abord enregistrés
        self._query = query
        # Pendant la frappe on filtre le dernier parcours ; en vidant la recherche,
        # on se resynchronise avec le disque.
        self._rebuild(rescan=not query)

    def _on_stop_search(self, entry: Gtk.SearchEntry):
        entry.set_text("")
        self.cb.leave_search()

    # ================================================================== menus contextuels

    def _attach_menu(self, widget: Gtk.Widget, build: Callable[[], Gio.MenuModel],
                     before: Callable[[], None] | None = None):
        """Clic droit (souris) et appui long (tactile) sur `widget` ; le menu est construit à la demande."""
        def show(gesture, x, y):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)   # pas de second menu plus haut
            if before is not None:
                before()
            menu = build()
            if menu is not None:
                popup_menu(widget, menu, x, y)

        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", lambda g, _n, x, y: show(g, x, y))
        widget.add_controller(click)
        long_press = Gtk.GestureLongPress(touch_only=True)
        long_press.connect("pressed", show)
        widget.add_controller(long_press)

    def _attach_row_menu(self, expander: Gtk.Widget, list_item: Gtk.ListItem):
        def build():
            row = list_item.get_item()
            if row is None:
                return None
            node = row.get_item().node
            return build_item_menu(node.path, node.is_dir, self.pins.is_pinned(node.path))

        def highlight():   # la ligne ciblée est mise en évidence
            if list_item.get_item() is not None:
                self.selection.set_selected(list_item.get_position())

        self._attach_menu(expander, build, before=highlight)

    def _on_pinned_context(self, path: Path, row: Gtk.Widget, x: float, y: float):
        popup_menu(row, build_item_menu(path, False, True), x, y)

    def _on_trash_context(self, entry: TrashEntry, row: Gtk.Widget, x: float, y: float):
        popup_menu(row, build_trash_menu(entry.trashed, entry.is_dir), x, y)

    def _add_delete_key(self, widget: Gtk.Widget, get_path: Callable[[], Path | None]):
        """Touche Suppr limitée à `widget` (jamais globale : elle effacerait du texte)."""
        controller = Gtk.EventControllerKey()

        def on_key(_c, keyval, _code, _state):
            if keyval not in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
                return False
            path = get_path()
            if path is not None:
                widget.activate_action("win.item-delete", GLib.Variant.new_string(str(path)))
            return True

        controller.connect("key-pressed", on_key)
        widget.add_controller(controller)

    # ================================================================== glisser-déposer

    @staticmethod
    def _new_drop_target() -> Gtk.DropTarget:
        """Cible acceptant un élément de la barre latérale ou des fichiers externes."""
        target = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        target.set_gtypes([Gdk.FileList, DragPayload.__gtype__])
        return target

    def _is_external(self, target: Gtk.DropTarget) -> bool:
        drop = target.get_current_drop()
        return (self._drag_src is None and drop is not None
                and drop.get_formats().contain_gtype(Gdk.FileList))

    @staticmethod
    def _dragged_path(value) -> Path | None:
        """Chemin source d'un dépôt interne, lu dans la charge utile elle-même."""
        if isinstance(value, DragPayload) and value.props.path:
            return Path(value.props.path)
        return None

    def _drop_kind(self, src: Path | None, src_is_dir: bool, target: Node | None) -> str | None:
        """
        Effet d'un dépôt de `src` sur `target` (None : la racine) :
        "move" (dans le dossier), "group" (nouveau dossier pour deux notes),
        "root" (remonte à la racine) ou None (refusé).
        """
        if src is None:
            return None
        if target is None:
            return "root" if src.parent != self.store.root else None
        if target.path == src:
            return None
        if target.is_dir:
            if src.parent == target.path:                       # déjà dedans
                return None
            if src_is_dir and src in target.path.parents:       # cycle
                return None
            return "move"
        return None if src_is_dir else "group"

    # --- source : chaque ligne

    def _attach_row_dnd(self, expander: Gtk.Widget, list_item: Gtk.ListItem):
        # MOVE : rangement dans la barre latérale ; COPY : ouverture dans un onglet.
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE | Gdk.DragAction.COPY)
        source.connect("prepare", self._on_drag_prepare, list_item)
        source.connect("drag-begin", self._on_drag_begin, list_item)
        source.connect("drag-end", self._on_drag_end)
        expander.add_controller(source)

        target = self._new_drop_target()
        target.connect("enter", self._on_row_motion, list_item)
        target.connect("motion", self._on_row_motion, list_item)
        target.connect("leave", self._on_row_leave, list_item)
        target.connect("drop", self._on_row_drop, list_item)
        expander.add_controller(target)

    def _on_drag_prepare(self, source, x, y, list_item):
        row = list_item.get_item()
        if row is None:
            return None
        source.set_icon(Gtk.WidgetPaintable.new(list_item.get_child()), int(x), int(y))
        return Gdk.ContentProvider.new_for_value(
            GObject.Value(DragPayload.__gtype__, DragPayload(row.get_item().node.path)))

    def _on_drag_begin(self, _source, _drag, list_item):
        row = list_item.get_item()
        if row is None:
            return
        node = row.get_item().node
        self._drag_src, self._drag_src_is_dir = node.path, node.is_dir
        self._drag_widget = list_item.get_child()
        self._drag_widget.add_css_class("lumina-dragging")

    def _on_drag_end(self, _source, _drag, _delete_data):
        if self._drag_widget is not None:
            self._drag_widget.remove_css_class("lumina-dragging")
        self._drag_widget = None
        self._drag_src = None
        self._clear_highlight()
        self._cancel_spring()

    # --- cibles : lignes

    def _on_row_motion(self, target, _x, _y, list_item):
        row = list_item.get_item()
        if row is None:
            return 0
        node = row.get_item().node
        if self._is_external(target):   # import : dans le dossier survolé, ou celui de la note
            if node.is_dir:
                self._set_highlight(list_item.get_child(), "move")
                if not row.get_expanded():
                    self._schedule_spring(node.path)
            else:
                self._clear_highlight()
                self._cancel_spring()
            return Gdk.DragAction.COPY
        kind = self._drop_kind(self._drag_src, self._drag_src_is_dir, node)
        if kind is None:
            # Refusé ICI, et rien au-dessus de la ligne n'accepte de dépôt : pas de
            # retour visuel, et lâcher l'élément ne fait rien.
            self._clear_highlight()
            self._cancel_spring()
            return 0
        self._set_highlight(list_item.get_child(), kind)
        if kind == "move" and not row.get_expanded():
            self._schedule_spring(node.path)
        else:
            self._cancel_spring()
        return Gdk.DragAction.MOVE

    def _on_row_leave(self, _target, list_item):
        if list_item.get_child() is self._hl_widget:
            self._clear_highlight()
        self._cancel_spring()

    def _on_row_drop(self, _target, value, _x, _y, list_item):
        self._clear_highlight()
        self._cancel_spring()
        row = list_item.get_item()
        if row is None:
            return False
        node = row.get_item().node
        if isinstance(value, Gdk.FileList):
            dest = node.path if node.is_dir else node.path.parent
            self._defer(self.cb.import_files, self._file_paths(value), dest)
            return True
        # 1. source : lue dans la charge utile ; destination : la ligne qui reçoit le dépôt.
        src = self._dragged_path(value)
        if src is None:
            return False
        kind = self._drop_kind(src, src.is_dir(), node)
        # 2. l'opération sur le disque (puis le rafraîchissement, avec la note
        #    déplacée sélectionnée) est faite par la fenêtre : storage.move / group_notes.
        if kind == "move":
            self._defer(self.cb.move, src, node.path)
        elif kind == "group":
            self._defer(self.cb.group, src, node.path)
        else:
            return False
        return True

    # --- cibles : racine

    def _add_root_drop_target(self, widget: Gtk.Widget):
        widget.add_css_class("lumina-root-zone")
        target = self._new_drop_target()
        target.connect("enter", self._on_root_motion)
        target.connect("motion", self._on_root_motion)
        target.connect("leave", self._on_zone_leave)
        target.connect("drop", self._on_root_drop)
        widget.add_controller(target)

    def _on_root_motion(self, target, _x, _y):
        if self._is_external(target):
            action = Gdk.DragAction.COPY
        elif self._drop_kind(self._drag_src, self._drag_src_is_dir, None) == "root":
            action = Gdk.DragAction.MOVE
        else:
            self._clear_highlight()
            return 0
        self._cancel_spring()
        self._set_highlight(target.get_widget(), "root")
        return action

    def _on_zone_leave(self, target):
        if target.get_widget() is self._hl_widget:
            self._clear_highlight()

    def _on_root_drop(self, _target, value, _x, _y):
        self._clear_highlight()
        if isinstance(value, Gdk.FileList):
            self._defer(self.cb.import_files, self._file_paths(value), self.store.root)
            return True
        src = self._dragged_path(value)
        if src is None or self._drop_kind(src, src.is_dir(), None) != "root":
            return False
        self._defer(self.cb.move, src, self.store.root)
        return True

    # --- cible : zone d'édition (la note glissée s'ouvre dans un nouvel onglet)

    def attach_open_target(self, widget: Gtk.Widget) -> None:
        widget.add_css_class("lumina-editor-zone")
        target = Gtk.DropTarget.new(DragPayload.__gtype__, Gdk.DragAction.COPY)
        target.connect("enter", self._on_open_motion)
        target.connect("motion", self._on_open_motion)
        target.connect("leave", self._on_zone_leave)
        target.connect("drop", self._on_open_drop)
        widget.add_controller(target)

    def _on_open_motion(self, target, _x, _y):
        if self._drag_src is None or self._drag_src_is_dir:   # seules les notes s'ouvrent
            self._clear_highlight()
            return 0
        self._cancel_spring()
        self._set_highlight(target.get_widget(), "open")
        return Gdk.DragAction.COPY

    def _on_open_drop(self, _target, value, _x, _y):
        self._clear_highlight()
        src = self._dragged_path(value)
        if src is None or src.is_dir():
            return False
        self._defer(self.cb.open_note, src, True)
        return True

    # --- utilitaires

    @staticmethod
    def _defer(func: Callable, *args) -> None:
        """
        Exécute `func` au prochain tour de boucle : on ne modifie pas le modèle
        (donc la ligne source) pendant que GTK finalise encore le glissement.
        """
        def run():
            func(*args)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(run)

    @staticmethod
    def _file_paths(files: Gdk.FileList) -> list[Path]:
        return [Path(f.get_path()) for f in files.get_files() if f.get_path() is not None]

    @staticmethod
    def _row_icon(expander: Gtk.Widget) -> Gtk.Image:
        return expander.get_child().get_first_child()

    def _set_highlight(self, widget: Gtk.Widget, kind: str):
        if widget is self._hl_widget and kind == self._hl_kind:
            return
        self._clear_highlight()
        widget.add_css_class(_HIGHLIGHT_CLASSES[kind])
        self._hl_widget, self._hl_kind = widget, kind
        if kind == "group":   # l'icône annonce la création d'un dossier
            icon = self._row_icon(widget)
            self._hl_icon = icon.get_icon_name()
            icon.set_from_icon_name("folder-new-symbolic")

    def _clear_highlight(self):
        widget = self._hl_widget
        if widget is None:
            return
        widget.remove_css_class(_HIGHLIGHT_CLASSES[self._hl_kind])
        if self._hl_kind == "group" and self._hl_icon:
            self._row_icon(widget).set_from_icon_name(self._hl_icon)
        self._hl_widget = self._hl_kind = self._hl_icon = None

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
