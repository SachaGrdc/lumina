"""
window.py — fenêtre principale de Lumina (Adw.ApplicationWindow).

Assemble les briques :
  * storage.py      accès disque (notes, dossiers, corbeille)       — sans GTK
  * pins.py         notes épinglées                                 — sans GTK
  * pinned.py       section « Épinglées » de la sidebar
  * trash_view.py   page « Corbeille » de la sidebar
  * context_menu.py menus contextuels (Gtk.PopoverMenu)
  * dialogs.py      dialogues Adw.AlertDialog
  * line_numbers.py numéros de ligne de l'éditeur
  * dnd.py          glisser-déposer (mixin)

Actions de la fenêtre :
  sans paramètre   win.new-note · win.new-folder · win.rename (F2)
                   win.focus-search · win.toggle-sidebar · win.empty-trash
  à état (booléen) win.show-trash   (page « Corbeille » de la sidebar)
  avec chemin ("s") win.item-new-note · win.item-new-folder · win.item-pin
                   win.item-rename · win.item-delete   (menu d'un élément)
                   win.trash-restore · win.trash-delete (menu de la corbeille)

Les raccourcis sont déclarés dans main.py. « Suppr » n'est volontairement PAS un
raccourci global (il effacerait du texte dans l'éditeur) : il n'agit que lorsque
le focus est dans la barre latérale.
"""

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from context_menu import build_item_menu, build_trash_menu, popup_menu  # noqa: E402
from dialogs import ask_text, confirm  # noqa: E402
from dnd import DragDropMixin  # noqa: E402
from i18n import _  # noqa: E402
from line_numbers import LineNumberGutter  # noqa: E402
from pinned import PinnedSection  # noqa: E402
from pins import PinStore  # noqa: E402
from storage import Node, NoteStore, StorageError, TrashEntry, filter_tree  # noqa: E402
from trash_view import TrashView  # noqa: E402

SAVE_DELAY_MS = 1000     # debouncing de la sauvegarde automatique
UNDO_TIMEOUT_S = 8       # durée d'affichage du toast « Annuler »
EDITOR_MARGIN = 40       # marge gauche de l'éditeur (réduite quand les numéros s'affichent)


class NoteItem(GObject.Object):
    """Enveloppe GObject autour d'un `Node` (requis par Gio.ListStore)."""

    __gtype_name__ = "LuminaNoteItem"

    def __init__(self, node: Node):
        super().__init__()
        self.node = node
        # Sous-modèle du dossier, créé à la demande puis conservé : on peut ainsi
        # le modifier en place (insertions / suppressions) sans recharger l'arbre.
        self.children_store: Gio.ListStore | None = None


class LuminaWindow(DragDropMixin, Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, store: NoteStore):
        super().__init__(application=app, title="Lumina",
                         default_width=1100, default_height=720)
        self.store = store
        self.pins = PinStore(store.root)

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

        self._init_dnd()
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
        self.sidebar_title = Adw.WindowTitle(title="Lumina", subtitle="")
        header.set_title_widget(self.sidebar_title)

        menu = Gio.Menu()
        menu.append(_("Nouvelle note"), "win.new-note")
        menu.append(_("Nouveau dossier"), "win.new-folder")
        new_button = Gtk.MenuButton(icon_name="list-add-symbolic", menu_model=menu,
                                    tooltip_text=_("Nouveau"))
        header.pack_start(new_button)

        # Menu principal (F10)
        main_menu = Gio.Menu()
        section = Gio.Menu()
        section.append(_("Corbeille"), "win.show-trash")
        main_menu.append_section(None, section)
        section = Gio.Menu()
        section.append(_("Préférences"), "app.preferences")
        main_menu.append_section(None, section)
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=main_menu,
                                     tooltip_text=_("Menu principal"), primary=True)
        header.pack_end(menu_button)

        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Rechercher…"),
                                            margin_start=12, margin_end=12,
                                            margin_top=6, margin_bottom=6)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("stop-search", self._on_stop_search)  # Échap

        # Section « Épinglées » (masquée tant qu'il n'y a aucune épingle)
        self.pinned = PinnedSection(self.store.root,
                                    on_activate=self._on_pinned_activated,
                                    on_context=self._on_pinned_context)
        self._add_delete_key(self.pinned.list_box, self.pinned.focused_path)
        separator = Gtk.Separator(margin_top=4, margin_bottom=4)
        self.pinned.bind_property("reveal-child", separator, "visible",
                                  GObject.BindingFlags.SYNC_CREATE)

        # Arborescence : TreeExpander > Box[icône, libellé]
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)
        factory.connect("unbind", self._on_factory_unbind)

        self.selection = Gtk.SingleSelection(autoselect=False, can_unselect=True)
        self.list_view = Gtk.ListView(model=self.selection, factory=factory,
                                      single_click_activate=True)
        self.list_view.add_css_class("navigation-sidebar")
        self.list_view.connect("activate", self._on_row_activated)
        self._add_delete_key(self.list_view, self._selected_tree_path)

        scrolled = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      child=self.list_view)

        # Zones « racine » du glisser-déposer : en-tête + espace vide sous la liste.
        # (Pas sur le champ de recherche : il accepterait le texte déposé.)
        self._add_root_drop_target(header)
        self._add_root_drop_target(scrolled)

        notes_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        notes_page.append(self.search_entry)
        notes_page.append(self.pinned)
        notes_page.append(separator)
        notes_page.append(scrolled)

        self.trash_view = TrashView(self.store.root, on_context=self._on_trash_context)

        self.sidebar_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.sidebar_stack.add_named(notes_page, "notes")
        self.sidebar_stack.add_named(self.trash_view, "trash")

        # Barre du bas : bascule Notes / Corbeille
        trash_toggle = Gtk.ToggleButton(action_name="win.show-trash",
                                        child=Adw.ButtonContent(icon_name="user-trash-symbolic",
                                                                label=_("Corbeille")))
        trash_toggle.add_css_class("flat")
        bottom = Gtk.Box(margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        bottom.append(trash_toggle)

        view = Adw.ToolbarView(content=self.sidebar_stack)
        view.add_top_bar(header)
        view.add_bottom_bar(bottom)
        return view

    def _build_content(self) -> Adw.ToolbarView:
        # En-tête : bouton de bascule de la sidebar + titre de la note au centre.
        header = Adw.HeaderBar(show_start_title_buttons=False)
        toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic",
                                  tooltip_text=_("Afficher / masquer la barre latérale (Ctrl+\\)"))
        self.split_view.bind_property(
            "show-sidebar", toggle, "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        header.pack_start(toggle)
        self.window_title = Adw.WindowTitle(title="Lumina", subtitle="")
        header.set_title_widget(self.window_title)

        # Page « aucune note »
        empty_button = Gtk.Button(label=_("Nouvelle note"), halign=Gtk.Align.CENTER,
                                  action_name="win.new-note")
        empty_button.add_css_class("suggested-action")
        empty_button.add_css_class("pill")
        empty = Adw.StatusPage(icon_name="document-edit-symbolic",
                               title=_("Aucune note ouverte"),
                               description=_("Sélectionnez une note dans la barre latérale "
                                             "ou créez-en une nouvelle."),
                               child=empty_button)

        # Page éditeur : TextView centré, sans bordure, avec de larges marges.
        self.buffer = Gtk.TextBuffer()
        self.buffer.connect("changed", self._on_buffer_changed)
        self.text_view = Gtk.TextView(buffer=self.buffer, vexpand=True,
                                      wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                      top_margin=40, bottom_margin=40,
                                      left_margin=EDITOR_MARGIN, right_margin=40,
                                      pixels_inside_wrap=4, pixels_below_lines=2)
        self.text_view.add_css_class("lumina-editor")
        self.gutter = LineNumberGutter(self.text_view)
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

    def set_line_numbers(self, enabled: bool):
        """Affiche ou masque les numéros de ligne de l'éditeur (préférences)."""
        self.text_view.set_gutter(Gtk.TextWindowType.LEFT, self.gutter if enabled else None)
        self.text_view.set_left_margin(12 if enabled else EDITOR_MARGIN)

    # ================================================================== État (reconstruction)

    def export_state(self) -> dict:
        """État à transmettre à la fenêtre qui remplace celle-ci (langue / dossier changés)."""
        self._flush()
        return {
            "note": self.current_path,
            "size": (self.get_width() or 1100, self.get_height() or 720),
            "maximized": self.is_maximized(),
            "sidebar": self.split_view.get_show_sidebar(),
            "trash": self.sidebar_stack.get_visible_child_name() == "trash",
        }

    def restore_state(self, state: dict):
        self.set_default_size(*state["size"])
        if state["maximized"]:
            self.maximize()
        self.split_view.set_show_sidebar(state["sidebar"])
        note = state["note"]
        if note is not None and note.is_file() and self.store.root in note.parents:
            self.open_note(note)
            self._reveal_in_tree(note)
        if state["trash"]:
            self._show_trash(True)

    # ================================================================== Actions

    def _build_actions(self):
        # Actions sans paramètre : raccourcis clavier et boutons.
        for name, callback in (
                ("new-note", lambda *_a: self._create_entry(False)),
                ("new-folder", lambda *_a: self._create_entry(True)),
                ("rename", lambda *_a: self._rename_path(self._keyboard_target())),
                ("focus-search", self._on_focus_search),
                ("toggle-sidebar", self._on_toggle_sidebar),
                ("empty-trash", self._on_empty_trash)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        # Bascule de la page « Corbeille » (case à cocher dans le menu, bouton du bas).
        show_trash = Gio.SimpleAction.new_stateful("show-trash", None,
                                                   GLib.Variant.new_boolean(False))
        show_trash.connect("change-state",
                           lambda _a, value: self._show_trash(value.get_boolean()))
        self.add_action(show_trash)

        # Actions des menus contextuels : elles reçoivent le chemin de l'élément cliqué.
        self._add_path_action("item-new-note", lambda p: self._create_entry(False, self._folder_of(p)))
        self._add_path_action("item-new-folder", lambda p: self._create_entry(True, self._folder_of(p)))
        self._add_path_action("item-pin", self._toggle_pin)
        self._add_path_action("item-rename", self._rename_path)
        self._add_path_action("item-delete", self._trash_path)
        self._add_path_action("trash-restore", self._restore_from_trash)
        self._add_path_action("trash-delete", self._delete_from_trash)

    def _add_path_action(self, name: str, callback: Callable[[Path], None]):
        action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
        action.connect("activate", lambda _a, param: callback(Path(param.get_string())))
        self.add_action(action)

    def _add_delete_key(self, widget: Gtk.Widget, get_path: Callable[[], Path | None]):
        """Touche Suppr limitée à `widget` (jamais globale : elle effacerait du texte)."""
        controller = Gtk.EventControllerKey()

        def on_key(_c, keyval, _code, _state):
            if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
                self._trash_path(get_path())
                return True
            return False

        controller.connect("key-pressed", on_key)
        widget.add_controller(controller)

    def toast(self, message: str, undo: Callable[[], None] | None = None):
        """Affiche un toast, avec un bouton « Annuler » si `undo` est fourni."""
        toast = Adw.Toast.new(message)
        if undo is not None:
            toast.set_button_label(_("Annuler"))
            toast.set_timeout(UNDO_TIMEOUT_S)
            toast.connect("button-clicked", lambda _t: undo())
        self.toast_overlay.add_toast(toast)

    # --- cible des actions clavier

    def _selected_tree_path(self) -> Path | None:
        row = self.selection.get_selected_item()
        return row.get_item().node.path if row is not None else None

    def _keyboard_target(self) -> Path | None:
        """Élément visé par F2 / Ctrl+N : ligne ayant le focus dans la sidebar, sinon la note ouverte."""
        focus = self.get_focus()
        if focus is not None:
            if focus is self.pinned or focus.is_ancestor(self.pinned):
                path = self.pinned.focused_path()
                if path is not None:
                    return path
            elif focus is self.list_view or focus.is_ancestor(self.list_view):
                path = self._selected_tree_path()
                if path is not None:
                    return path
        return self.current_path

    def _folder_of(self, path: Path | None) -> Path:
        """Dossier de `path` s'il en est un, sinon son dossier parent (racine par défaut)."""
        if path is None:
            return self.store.root
        return path if path.is_dir() else path.parent

    # --- raccourcis globaux

    def _on_toggle_sidebar(self, *_args):
        self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())

    def _on_focus_search(self, *_args):
        self.split_view.set_show_sidebar(True)  # révèle la sidebar si elle est masquée
        self._show_trash(False)
        GLib.idle_add(self._focus_search_idle)

    def _focus_search_idle(self):
        self.search_entry.grab_focus()  # sélectionne aussi le texte déjà saisi
        return GLib.SOURCE_REMOVE

    def _on_stop_search(self, entry: Gtk.SearchEntry):
        """Échap dans la recherche : vide le filtre et rend le focus à l'éditeur / la liste."""
        entry.set_text("")
        (self.text_view if self.current_path is not None else self.list_view).grab_focus()

    # ================================================================== Menu contextuel

    def _attach_row_menu(self, widget: Gtk.Widget, list_item: Gtk.ListItem):
        """Clic droit (souris) et appui long (tactile) sur une ligne de l'arborescence."""
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)

        def on_click(gesture, _n_press, x, y):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)  # la ListView n'en fait rien
            self._show_row_menu(list_item, widget, x, y)

        click.connect("pressed", on_click)
        widget.add_controller(click)

        long_press = Gtk.GestureLongPress(touch_only=True)
        long_press.connect("pressed", lambda _g, x, y: self._show_row_menu(list_item, widget, x, y))
        widget.add_controller(long_press)

    def _show_row_menu(self, list_item: Gtk.ListItem, widget: Gtk.Widget, x: float, y: float):
        row = list_item.get_item()
        if row is None:
            return
        node = row.get_item().node
        self.selection.set_selected(list_item.get_position())  # la ligne ciblée est mise en évidence
        popup_menu(widget, build_item_menu(node.path, node.is_dir, self.pins.is_pinned(node.path)), x, y)

    def _on_pinned_context(self, path: Path, row: Gtk.Widget, x: float, y: float):
        popup_menu(row, build_item_menu(path, False, True), x, y)

    def _on_trash_context(self, entry: TrashEntry, row: Gtk.Widget, x: float, y: float):
        popup_menu(row, build_trash_menu(entry.trashed, entry.is_dir), x, y)

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
        self._refresh_pins()
        self._refresh_trash()

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
        self._refresh_pins()
        self._refresh_trash()

    def _sync_store(self, store: Gio.ListStore, nodes: list[Node]):
        """
        Aligne `store` sur `nodes` en supprimant / insérant uniquement la différence.
        Les éléments conservés gardent leur sous-modèle (donc leur état déplié) ;
        la synchronisation descend dans les sous-modèles déjà créés.
        """
        wanted = {n.path: n for n in nodes}

        # 1. Retire ce qui a disparu — ou dont l'affichage doit changer (nom modifié,
        #    même seulement par la casse : Path("a") == Path("A") sous Windows).
        i = 0
        while i < store.get_n_items():
            item = store.get_item(i)
            node = wanted.get(item.node.path)
            if (node is not None and node.path.name == item.node.path.name
                    and node.is_dir == item.node.is_dir):
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

    def _reveal_in_tree(self, path: Path):
        """Déplie les dossiers parents de `path` puis le sélectionne dans l'arborescence."""
        if self._model_filtered:
            return
        self._expand_rows({p for p in path.parents if self.store.root in p.parents})
        self._select_path(path)

    def _expanded_snapshot(self) -> set[Path]:
        """Dossiers actuellement ouverts (pour suivre un dossier renommé / déplacé)."""
        if self.tree_model is None or self._model_filtered:
            return set()
        return self._collect_expanded()

    @staticmethod
    def _remap_paths(paths: set[Path], old: Path, new: Path) -> set[Path]:
        """Transpose les chemins situés sous `old` vers `new` (les autres sont ignorés)."""
        out = set()
        for p in paths:
            try:
                out.add(new / p.relative_to(old))
            except ValueError:
                pass
        return out

    # --- fabrique de lignes

    def _on_factory_setup(self, _factory, list_item):
        box = Gtk.Box(spacing=8)
        box.append(Gtk.Image())
        box.append(Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        expander = Gtk.TreeExpander(child=box)
        expander.add_css_class("lumina-row")
        list_item.set_child(expander)
        self._attach_row_dnd(expander, list_item)    # glisser-déposer (dnd.py)
        self._attach_row_menu(expander, list_item)   # menu contextuel

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
        self._dnd_row_unbound(expander)
        expander.set_list_row(None)

    # --- interactions

    def _on_row_activated(self, _list_view, position):
        row = self.selection.get_item(position)
        node = row.get_item().node
        if node.is_dir:
            row.set_expanded(not row.get_expanded())
        else:
            self.open_note(node.path)

    def _on_pinned_activated(self, path: Path):
        self.open_note(path)
        self._reveal_in_tree(path)

    def _on_search_changed(self, entry: Gtk.SearchEntry):
        self._query = entry.get_text().strip()
        self.reload_tree()

    # ================================================================== Épingles

    def _refresh_pins(self):
        """Resynchronise la section « Épinglées » (masquée pendant une recherche)."""
        self.pins.prune()  # oublie les notes supprimées en dehors de Lumina
        self.pinned.set_pins(self.pins.paths())
        self.pinned.set_enabled(not self._query)
        self.pinned.set_current(self.current_path)

    def _toggle_pin(self, path: Path):
        if path.is_dir():  # on n'épingle que des notes
            return
        pinned = self.pins.toggle(path)
        self._refresh_pins()
        self.toast(_("Note épinglée") if pinned else _("Note désépinglée"))

    # ================================================================== Création

    def _create_entry(self, is_folder: bool, parent: Path | None = None):
        if parent is None:
            parent = self._folder_of(self._keyboard_target())

        def on_name(name: str):
            try:
                if is_folder:
                    path = self.store.create_folder(parent, name)
                else:
                    path = self.store.create_note(parent, name)
            except StorageError as e:
                self.toast(str(e))
                return
            if self._query:  # efface un éventuel filtre pour que le nouvel élément soit visible
                self._query = ""
                self.search_entry.set_text("")
            self._show_trash(False)
            self.refresh_tree(select_path=path)
            if not is_folder:
                self.open_note(path)

        where = (_("Créé à la racine") if parent == self.store.root
                 else _("Créé dans : {folder}").format(
                     folder=parent.relative_to(self.store.root).as_posix()))
        ask_text(self,
                 heading=_("Nouveau dossier") if is_folder else _("Nouvelle note"),
                 body=where,
                 default=_("Nouveau dossier") if is_folder else _("Sans titre"),
                 ok_label=_("Créer"),
                 on_ok=on_name)

    # ================================================================== Renommer

    def _rename_path(self, path: Path | None):
        if path is None or path == self.store.root:
            return
        current = path.name if path.is_dir() else path.stem
        body = (_("Nouveau nom pour le dossier « {name} »") if path.is_dir()
                else _("Nouveau nom pour la note « {name} »")).format(name=current)
        ask_text(self,
                 heading=_("Renommer"),
                 body=body,
                 default=current,
                 ok_label=_("Renommer"),
                 on_ok=lambda name: self._do_rename(path, name))

    def _do_rename(self, path: Path, name: str):
        self._flush()  # la note ouverte est enregistrée avant que son fichier ne bouge
        snapshot = self._expanded_snapshot()
        try:
            new = self.store.rename(path, name)
        except StorageError as e:
            self.toast(str(e))
            return
        if new == path and new.name == path.name:
            return  # nom inchangé
        self._path_moved(path, new)
        self.refresh_tree(select_path=new, expand_paths=self._remap_paths(snapshot, path, new))

    # ================================================================== Corbeille

    def _trash_path(self, path: Path | None):
        if path is None or path == self.store.root:
            return
        is_dir = path.is_dir()
        name = path.name if is_dir else path.stem
        confirm(self,
                heading=_("Mettre « {name} » à la corbeille ?").format(name=name),
                body=(_("Le dossier et tout son contenu seront déplacés dans la corbeille.")
                      if is_dir else _("La note sera déplacée dans la corbeille."))
                     + " " + _("Vous pourrez la restaurer depuis la corbeille."),
                ok_label=_("Mettre à la corbeille"),
                on_ok=lambda: self._do_trash(path, name))

    def _do_trash(self, path: Path, name: str):
        self._flush()
        try:
            entry = self.store.move_to_trash(path)
        except StorageError as e:
            self.toast(str(e))
            return
        unpinned = self.pins.remove_under(path)  # conservées pour pouvoir annuler
        if self.current_path is not None and (
                self.current_path == path or path in self.current_path.parents):
            self._close_note()
        self.refresh_tree()
        self.toast(_("« {name} » mis à la corbeille").format(name=name),
                   undo=lambda: self._undo_trash(entry, unpinned))

    def _undo_trash(self, entry: TrashEntry, unpinned: list[Path]):
        try:
            restored = self.store.restore(entry)
        except StorageError as e:
            self.toast(str(e))
            return
        for pin in unpinned:  # rétablit aussi les épingles (un dossier peut en contenir)
            self.pins.pin(restored / pin.relative_to(entry.original))
        self.refresh_tree(select_path=restored)
        self._toast_restored(restored)

    def _toast_restored(self, restored: Path):
        name = restored.name if restored.is_dir() else restored.stem
        self.toast(_("« {name} » restauré").format(name=name))

    def _show_trash(self, show: bool):
        """Bascule la barre latérale entre l'arborescence et la corbeille."""
        self.lookup_action("show-trash").set_state(GLib.Variant.new_boolean(show))
        self.sidebar_stack.set_visible_child_name("trash" if show else "notes")
        self.sidebar_title.set_title(_("Corbeille") if show else "Lumina")
        if show:
            self._refresh_trash()

    def _refresh_trash(self):
        entries = self.store.list_trash()
        self.trash_view.set_entries(entries)
        self.lookup_action("empty-trash").set_enabled(bool(entries))

    def _trash_entry(self, trashed: Path) -> TrashEntry | None:
        for entry in self.store.list_trash():
            if entry.trashed == trashed:
                return entry
        self.toast(_("« {name} » n'est plus dans la corbeille.").format(name=trashed.name))
        self._refresh_trash()
        return None

    def _restore_from_trash(self, trashed: Path):
        entry = self._trash_entry(trashed)
        if entry is None:
            return
        try:
            restored = self.store.restore(entry)
        except StorageError as e:
            self.toast(str(e))
            return
        self.refresh_tree(select_path=restored)
        self._toast_restored(restored)

    def _delete_from_trash(self, trashed: Path):
        entry = self._trash_entry(trashed)
        if entry is None:
            return

        def do_delete():
            try:
                self.store.delete_from_trash(entry)
            except StorageError as e:
                self.toast(str(e))
            self._refresh_trash()

        confirm(self,
                heading=_("Supprimer « {name} » définitivement ?").format(name=entry.title),
                body=_("Cette action est irréversible."),
                ok_label=_("Supprimer"),
                on_ok=do_delete)

    def _on_empty_trash(self, *_args):
        count = len(self.store.list_trash())
        if not count:
            return

        def do_empty():
            try:
                self.store.empty_trash()
            except StorageError as e:
                self.toast(str(e))
            self._refresh_trash()
            self.toast(_("Corbeille vidée"))

        confirm(self,
                heading=_("Vider la corbeille ?"),
                body=(_("1 élément sera supprimé définitivement.") if count == 1
                      else _("{count} éléments seront supprimés définitivement.").format(count=count))
                     + " " + _("Cette action est irréversible."),
                ok_label=_("Vider la corbeille"),
                on_ok=do_empty)

    # ================================================================== Suivi des chemins

    def _path_moved(self, old: Path, new: Path):
        """À appeler après tout renommage / déplacement : éditeur et épingles suivent."""
        self._remap_current(old, new)
        self.pins.remap(old, new)
        if self.current_path is not None:
            self.window_title.set_title(self.current_path.stem)

    def _remap_current(self, old: Path, new: Path):
        """Si la note ouverte se trouvait dans `old`, suit son déplacement vers `new`."""
        if self.current_path is None:
            return
        try:
            relative = self.current_path.relative_to(old)
        except ValueError:
            return
        self.current_path = new / relative

    # ================================================================== Éditeur

    def open_note(self, path: Path):
        self._flush()  # enregistre la note précédente avant d'en changer
        try:
            text = self.store.read_note(path)
        except StorageError as e:
            self.toast(str(e))
            return

        self._set_buffer_text(text)
        self.current_path = path
        self.window_title.set_title(path.stem)
        self.window_title.set_subtitle("")
        self.stack.set_visible_child_name("editor")
        self.pinned.set_current(path)
        self.text_view.grab_focus()

        if self.split_view.get_collapsed():  # mode étroit : on révèle l'éditeur
            self.split_view.set_show_sidebar(False)

    def _close_note(self):
        """Revient à l'état vide (la note ouverte a été supprimée)."""
        self._set_buffer_text("")
        self.current_path = None
        self.window_title.set_title("Lumina")
        self.window_title.set_subtitle("")
        self.stack.set_visible_child_name("empty")
        self.pinned.set_current(None)

    def _set_buffer_text(self, text: str):
        self._loading = True
        self.buffer.begin_irreversible_action()  # Ctrl+Z ne doit pas « décharger » la note
        self.buffer.set_text(text)
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self.buffer.end_irreversible_action()
        self._loading = False
        self._dirty = False

    # --- sauvegarde automatique (debouncing)

    def _on_buffer_changed(self, _buffer):
        if self._loading or self.current_path is None:
            return
        self._dirty = True
        self.window_title.set_subtitle(_("Modifications en cours…"))
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
        self.window_title.set_subtitle(_("Enregistré"))

    def _on_close_request(self, _window):
        self._flush()
        return False  # laisse la fenêtre se fermer
