#!/usr/bin/env python3
"""
Lumina — prise de notes ultra-minimaliste (GTK4 + Libadwaita).

Ce fichier ne contient que l'interface graphique. Toute la logique de fichiers
(parcours, création, lecture, écriture atomique, filtrage) est dans storage.py.

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
SAVE_DELAY_MS = 1000  # debouncing de la sauvegarde automatique

CSS = """
textview.lumina-editor,
textview.lumina-editor text {
    background: transparent;
    font-size: 115%;
}
"""


class NoteItem(GObject.Object):
    """Enveloppe GObject autour d'un `Node` (requis par Gio.ListStore)."""

    __gtype_name__ = "LuminaNoteItem"

    def __init__(self, node: Node):
        super().__init__()
        self.node = node


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
        self._model_filtered = False
        self._expanded: set[Path] = set()
        self._query = ""

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

    def reload_tree(self, select_path: Path | None = None):
        """Reconstruit le modèle depuis le disque en conservant l'état d'expansion."""
        # Mémorise les dossiers ouverts (uniquement hors recherche : en recherche
        # tout est déplié automatiquement et ne doit pas polluer l'état).
        if self.tree_model is not None and not self._model_filtered:
            self._expanded = self._collect_expanded()

        root = self.store.scan()
        filtered = bool(self._query)
        if filtered:
            root = filter_tree(root, self._query)

        root_store = Gio.ListStore(item_type=NoteItem)
        for child in root.children:
            root_store.append(NoteItem(child))

        # TreeListModel : chaque dossier fournit un sous-modèle via `_create_children`.
        self.tree_model = Gtk.TreeListModel.new(root_store, False, filtered,
                                                self._create_children)
        self._model_filtered = filtered
        self.selection.set_model(self.tree_model)

        if not filtered:
            wanted = set(self._expanded)
            if select_path is not None:
                wanted.update(p for p in select_path.parents if p != self.store.root)
            self._expanded = wanted
            self._expand_rows(wanted)

        target = select_path or self.current_path
        if target is not None:
            self._select_path(target)

    @staticmethod
    def _create_children(item: NoteItem):
        if not item.node.is_dir:
            return None
        children = Gio.ListStore(item_type=NoteItem)
        for child in item.node.children:
            children.append(NoteItem(child))
        return children

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

    def _select_path(self, path: Path):
        for i in range(self.tree_model.get_n_items()):
            if self.tree_model.get_row(i).get_item().node.path == path:
                self.selection.set_selected(i)
                try:
                    self.list_view.scroll_to(i, Gtk.ListScrollFlags.NONE, None)
                except (AttributeError, TypeError):  # GTK < 4.12
                    pass
                return

    # --- fabrique de lignes

    def _on_factory_setup(self, _factory, list_item):
        box = Gtk.Box(spacing=8)
        box.append(Gtk.Image())
        box.append(Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        expander = Gtk.TreeExpander(child=box)
        list_item.set_child(expander)

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
        list_item.get_child().set_list_row(None)

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
            self.reload_tree(select_path=path)
            if not is_folder:
                self.open_note(path)

        self._ask_name(
            heading="Nouveau dossier" if is_folder else "Nouvelle note",
            body=f"Créé dans : {parent.relative_to(self.store.root) or 'racine'}"
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
