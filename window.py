"""
window.py — fenêtre principale de Lumina (Adw.ApplicationWindow).

La fenêtre ORCHESTRE ; elle ne dessine ni l'arborescence ni l'éditeur :
  * sidebar.py      barre latérale (arborescence, recherche, épingles, corbeille)
  * tabs.py         onglets de l'éditeur ; editor.py : contenu d'un onglet
  * storage.py      toutes les opérations sur le disque (StorageManager)
  * pins.py         notes épinglées
  * dialogs.py      dialogues Adw.AlertDialog (nom, confirmation)
  * printing.py     impression / export PDF

Une opération suit toujours le même chemin : action « win.* » -> dialogue
éventuel -> StorageManager -> suivi des onglets et épingles (_path_moved) ->
`sidebar.refresh()`. Les erreurs du disque (StorageError) s'affichent dans un
toast ; toute autre exception est interceptée par main.py (sys.excepthook).

Actions de la fenêtre :
  sans paramètre   win.new-note · win.new-folder · win.rename (F2) · win.save
                   win.new-tab · win.close-tab · win.print
                   win.focus-search · win.toggle-sidebar · win.empty-trash
  à état (booléen) win.show-trash   (page « Corbeille », bouton en bas de la barre latérale)
  avec chemin ("s") win.item-open-tab · win.item-new-note · win.item-new-folder
                   win.item-pin · win.item-rename · win.item-delete (menus contextuels)
                   win.trash-restore · win.trash-delete (menu de la corbeille)

Les raccourcis sont déclarés dans main.py. « Suppr » n'est volontairement PAS un
raccourci global (il effacerait du texte dans l'éditeur) : la barre latérale
le gère elle-même, quand elle a le focus.
"""

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from dialogs import ask_text, confirm  # noqa: E402
from editor import NotePage  # noqa: E402
from i18n import _  # noqa: E402
from pins import PinStore  # noqa: E402
from printing import print_note  # noqa: E402
from sidebar import Sidebar, SidebarCallbacks  # noqa: E402
from storage import StorageError, StorageManager, TrashEntry  # noqa: E402
from tabs import NoteTabs  # noqa: E402

UNDO_TIMEOUT_S = 8       # durée d'affichage du toast « Annuler »


class LuminaWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, store: StorageManager):
        super().__init__(application=app, title="Lumina",
                         default_width=1100, default_height=720)
        self.store = store
        self.pins = PinStore(store.root)
        self._line_numbers = False

        self._build_ui()
        self._build_actions()
        self.sidebar.refresh_trash()   # état initial de « Vider la corbeille »
        self.connect("close-request", self._on_close_request)
        # Retour dans Lumina : l'arborescence reprend les changements faits ailleurs.
        self.connect("notify::is-active", self._on_active_changed)

    # ================================================================== UI

    def _build_ui(self):
        self.split_view = Adw.OverlaySplitView(
            min_sidebar_width=240, max_sidebar_width=340, sidebar_width_fraction=0.26)
        self.sidebar = Sidebar(self.store, self.pins, SidebarCallbacks(
            open_note=lambda path, new_tab: self.open_note(path, new_tab=new_tab),
            before_search=self._flush,
            leave_search=self._focus_editor,
            move=self._move_item,
            group=self._group_notes,
            import_files=self._import_files,
            trash_changed=self._on_trash_changed,
        ))
        self.split_view.set_sidebar(self.sidebar)
        self.split_view.set_content(self._build_content())
        self.set_content(self.split_view)

        # Sur fenêtre étroite, la barre latérale passe en superposition.
        try:
            bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
            bp.add_setter(self.split_view, "collapsed", True)
            self.add_breakpoint(bp)
        except (AttributeError, TypeError):   # libadwaita < 1.4
            pass

    def _build_content(self) -> Adw.ToolbarView:
        # En-tête : bouton de bascule de la barre latérale + titre de la note.
        header = Adw.HeaderBar(show_start_title_buttons=False)
        toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic",
                                  tooltip_text=_("Afficher / masquer la barre latérale (Ctrl+\\)"))
        self.split_view.bind_property(
            "show-sidebar", toggle, "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        header.pack_start(toggle)
        self.window_title = Adw.WindowTitle(title="Lumina", subtitle="")
        header.set_title_widget(self.window_title)

        # Onglets : la barre n'apparaît qu'à partir de deux onglets ; sans aucun
        # onglet, l'état vide s'affiche à la place de l'éditeur.
        self.tabs = NoteTabs(on_selected=self._on_tab_selected,
                             empty_page=self._build_empty_state())

        self.toast_overlay = Adw.ToastOverlay(child=self.tabs.widget)
        self.sidebar.attach_open_target(self.toast_overlay)   # note glissée -> nouvel onglet
        view = Adw.ToolbarView(content=self.toast_overlay)
        view.add_top_bar(header)
        view.add_top_bar(self.tabs.bar)
        return view

    @staticmethod
    def _build_empty_state() -> Adw.StatusPage:
        """Affiché quand aucune note n'est ouverte (aucun onglet)."""
        return Adw.StatusPage(
            icon_name="document-edit-symbolic",
            title=_("Aucune note ouverte"),
            description=_("Sélectionnez une note dans la barre latérale "
                          "ou créez-en une nouvelle avec Ctrl + N."),
            vexpand=True)

    def _make_page(self) -> NotePage:
        page = NotePage(self.store, on_materialize=self._materialize_draft, on_error=self.toast)
        page.set_line_numbers(self._line_numbers)
        return page

    def set_line_numbers(self, enabled: bool):
        """Affiche ou masque les numéros de ligne de l'éditeur (préférences)."""
        self._line_numbers = enabled
        for page in self.tabs.pages():
            page.set_line_numbers(enabled)

    @property
    def current_page(self) -> NotePage | None:
        """Onglet actif ; None quand aucun onglet n'est ouvert (état vide)."""
        return self.tabs.current

    @property
    def current_path(self) -> Path | None:
        """Note de l'onglet actif (None : page blanche, ou aucun onglet)."""
        page = self.tabs.current
        return page.path if page is not None else None

    def toast(self, message: str, undo: Callable[[], None] | None = None):
        """Affiche un toast, avec un bouton « Annuler » si `undo` est fourni."""
        toast = Adw.Toast.new(message)
        if undo is not None:
            toast.set_button_label(_("Annuler"))
            toast.set_timeout(UNDO_TIMEOUT_S)
            toast.connect("button-clicked", lambda _t: undo())
        self.toast_overlay.add_toast(toast)

    # ================================================================== État (reconstruction)

    def export_state(self) -> dict:
        """État à transmettre à la fenêtre qui remplace celle-ci (langue / dossier changés)."""
        self._flush()
        tabs, active = self.tabs.session()
        return {
            "tabs": tabs,
            "active_tab": active,
            "size": (self.get_width() or 1100, self.get_height() or 720),
            "maximized": self.is_maximized(),
            "sidebar": self.split_view.get_show_sidebar(),
            "trash": self.sidebar.trash_shown,
        }

    def restore_state(self, state: dict):
        self.set_default_size(*state["size"])
        if state["maximized"]:
            self.maximize()
        self.split_view.set_show_sidebar(state["sidebar"])
        self.restore_tabs(state["tabs"], state["active_tab"])
        if state["trash"]:
            self._show_trash(True)

    def restore_tabs(self, tabs: list[dict], active: int = 0):
        """
        Ouverture : rouvre les onglets de la session précédente (note et position du
        curseur). Sans rien à rouvrir, l'état vide « Aucune note ouverte » s'affiche.
        Les notes disparues ou illisibles sont ignorées.
        """
        pages: list[NotePage] = []
        for tab in tabs:
            path = Path(tab["path"])
            if (not path.is_file() or not self.store.contains(path) or self.store.in_trash(path)
                    or any(p.path == path for p in pages)):
                continue
            page = self._make_page()
            if page.load(path):
                page.restore_cursor(tab.get("cursor", 0))
                pages.append(page)

        for page in pages:
            self.tabs.add(page, select=False)
        selected = pages[min(active, len(pages) - 1)] if pages else None
        if selected is not None:
            self.tabs.select(selected)
        self._on_tab_selected(selected)   # même si la sélection n'a pas changé

    # ================================================================== Actions

    def _build_actions(self):
        for name, callback in (
                ("new-note", lambda *_a: self._new_note()),
                ("new-folder", lambda *_a: self._new_folder()),
                ("rename", lambda *_a: self._rename_path(self._keyboard_target())),
                ("save", lambda *_a: self._flush()),   # Ctrl+S, facultatif et silencieux
                ("new-tab", lambda *_a: self._new_tab()),
                ("close-tab", lambda *_a: self.current_page and self.tabs.close(self.current_page)),
                ("print", lambda *_a: self._print()),
                ("focus-search", lambda *_a: self._focus_search()),
                ("toggle-sidebar", lambda *_a: self.split_view.set_show_sidebar(
                    not self.split_view.get_show_sidebar())),
                ("empty-trash", lambda *_a: self._empty_trash())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        # Bascule de la page « Corbeille » (case à cocher du menu, bouton du bas).
        show_trash = Gio.SimpleAction.new_stateful("show-trash", None,
                                                   GLib.Variant.new_boolean(False))
        show_trash.connect("change-state", lambda _a, value: self._show_trash(value.get_boolean()))
        self.add_action(show_trash)

        # Menus contextuels : l'action reçoit le chemin de l'élément cliqué.
        # « Nouvelle note / Nouveau dossier (ici) » n'existent que sur un dossier ou la
        # zone vide (racine) : le chemin EST la destination, sans rien deviner.
        for name, callback in (
                ("item-open-tab", lambda p: self.open_note(p, new_tab=True)),
                ("item-new-note", self._new_note),
                ("item-new-folder", self._new_folder),
                ("item-pin", self._toggle_pin),
                ("item-rename", self._rename_path),
                ("item-delete", self._trash_path),
                ("trash-restore", self._restore_from_trash),
                ("trash-delete", self._delete_from_trash)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate",
                           lambda _a, param, cb=callback: cb(Path(param.get_string())))
            self.add_action(action)

    def _keyboard_target(self) -> Path | None:
        """Élément visé par F2 / Ctrl+N : celui qui a le focus dans la barre latérale, sinon la note ouverte."""
        return self.sidebar.focused_path() or self.current_path

    def _folder_of(self, path: Path | None) -> Path:
        """Dossier de `path` s'il en est un, sinon son dossier parent (racine par défaut)."""
        if path is None:
            return self.store.root
        return path if path.is_dir() else path.parent

    def _focus_search(self):
        self.split_view.set_show_sidebar(True)   # révèle la barre latérale si elle est masquée
        self._show_trash(False)
        self.sidebar.focus_search()

    def _toggle_pin(self, path: Path):
        if path.is_dir():   # on n'épingle que des notes
            return
        pinned = self.pins.toggle(path)
        self.sidebar.refresh_pins()
        self.toast(_("Note épinglée") if pinned else _("Note désépinglée"))

    # ================================================================== Création

    def _new_note(self, folder: Path | None = None):
        """Nouvelle note : demande un nom, crée la note, la sélectionne et l'ouvre."""
        self._ask_create(folder, is_folder=False)

    def _new_folder(self, folder: Path | None = None):
        """Nouveau dossier : demande un nom, crée le dossier et le sélectionne."""
        self._ask_create(folder, is_folder=True)

    def _ask_create(self, folder: Path | None, is_folder: bool):
        """
        `folder` : destination (par défaut, le dossier de l'élément ciblé au clavier).
        Le dialogue propose un nom libre (« Sans titre », « Sans titre 1 »…) ; un nom
        déjà pris est numéroté de même par StorageManager.
        """
        if folder is None:
            folder = self._folder_of(self._keyboard_target())
        default = self.store.suggest_name(
            folder, _("Nouveau dossier") if is_folder else _("Sans titre"), is_folder)

        def on_name(name: str):
            try:
                path = (self.store.create_folder(folder, name) if is_folder
                        else self.store.create_note(folder, name))
            except StorageError as e:
                self.toast(str(e))
                self.sidebar.refresh()   # ex. dossier supprimé hors de Lumina
                return
            self.sidebar.clear_search()   # le nouvel élément doit être visible
            self._show_trash(False)
            self.sidebar.refresh(select=path)
            if not is_folder:
                self.open_note(path)

        ask_text(self,
                 heading=_("Nouveau dossier") if is_folder else _("Nouvelle note"),
                 body=self._location_label(folder),
                 default=default,
                 ok_label=_("Créer"),
                 on_ok=on_name)

    def _location_label(self, folder: Path) -> str:
        if folder == self.store.root:
            return _("Créé à la racine")
        try:
            relative = folder.relative_to(self.store.root).as_posix()
        except ValueError:
            relative = folder.name
        return _("Créé dans : {folder}").format(folder=relative)

    # ================================================================== Renommage, déplacement, import

    def _rename_path(self, path: Path | None):
        if path is None or path == self.store.root:
            return
        is_dir = path.is_dir()
        current = path.name if is_dir else path.stem
        ask_text(self,
                 heading=_("Renommer"),
                 body=(_("Nouveau nom pour le dossier « {name} »") if is_dir
                       else _("Nouveau nom pour la note « {name} »")).format(name=current),
                 default=current,
                 ok_label=_("Renommer"),
                 on_ok=lambda name: self._do_rename(path, name))

    def _do_rename(self, path: Path, name: str):
        self._flush()   # les onglets sont enregistrés avant que leurs fichiers ne bougent
        expanded = self.sidebar.expanded_paths()
        try:
            new = self.store.rename(path, name)
        except StorageError as e:
            self.toast(str(e))
            self.sidebar.refresh()
            return
        if new.name == path.name:
            return   # nom inchangé
        self._path_moved(path, new)
        self.sidebar.refresh(select=new, expand=self._remap_paths(expanded, path, new))

    def _move_item(self, src: Path, folder: Path):
        """Glisser-déposer dans la barre latérale : `src` va dans `folder`."""
        self._flush()
        expanded = self.sidebar.expanded_paths()
        name = src.name if src.is_dir() else src.stem
        try:
            new = self.store.move(src, folder)
        except StorageError as e:
            self.toast(str(e))
            self.sidebar.refresh()
            return
        self._path_moved(src, new)
        self.sidebar.refresh(select=new, expand=self._remap_paths(expanded, src, new))
        if folder == self.store.root:
            self.toast(_("« {name} » déplacé à la racine").format(name=name))
        else:
            self.toast(_("« {name} » déplacé dans « {folder} »").format(name=name, folder=folder.name))

    def _group_notes(self, src: Path, target: Path):
        """Note déposée sur une note : un dossier (nommé d'après la cible) les regroupe."""
        self._flush()
        try:
            folder, new_src, new_target = self.store.group_notes(src, target)
        except StorageError as e:
            self.toast(str(e))
            self.sidebar.refresh()
            return
        self._path_moved(src, new_src)
        self._path_moved(target, new_target)
        self.sidebar.refresh(select=folder, expand={folder})
        self.toast(_("Dossier « {name} » créé").format(name=folder.name))

    def _import_files(self, paths: list[Path], folder: Path):
        """Fichiers déposés depuis le gestionnaire de fichiers : copiés dans `folder`."""
        imported: list[Path] = []
        error = None
        for path in paths:
            try:
                imported.append(self.store.import_note(path, folder))
            except StorageError as e:
                error = str(e)
        if imported:
            self.sidebar.refresh(select=imported[-1])
            if len(imported) == 1:
                self.open_note(imported[0])
                self.toast(_("« {name} » importé").format(name=imported[0].stem))
            else:
                self.toast(_("{count} notes importées").format(count=len(imported)))
        if error is not None:   # au moins un fichier refusé (dossier, image…)
            self.toast(error)

    def open_files(self, paths: list[Path]):
        """
        Fichiers reçus du système (Ouvrir avec…). Une note du dossier de notes est
        ouverte telle quelle ; un fichier situé ailleurs y est d'abord copié. Chaque
        fichier a son onglet (une page blanche encore vide est réutilisée).
        """
        for path in paths:
            path = path.resolve()
            if not self.store.contains(path) or self.store.in_trash(path):
                try:
                    path = self.store.import_note(path, self.store.root)
                except StorageError as e:
                    self.toast(str(e))
                    continue
                self.sidebar.refresh()
                self.toast(_("« {name} » importé").format(name=path.stem))
            page = self.current_page
            self.open_note(path, new_tab=page is None or page.path is not None)

    # ================================================================== Corbeille

    def _trash_path(self, path: Path | None):
        """Mise à la corbeille immédiate : réversible (toast « Annuler », page Corbeille),
        la confirmation est réservée aux suppressions définitives."""
        if path is None or path == self.store.root:
            return
        name = path.name if path.is_dir() else path.stem
        self._flush()
        try:
            entry = self.store.move_to_trash(path)
        except StorageError as e:
            self.toast(str(e))
            self.sidebar.refresh()
            return
        unpinned = self.pins.remove_under(path)   # conservées pour pouvoir annuler
        for page in self.tabs.pages():   # ferme les onglets des notes mises à la corbeille
            if page.is_under(path):
                self.tabs.close(page)
        self.sidebar.refresh()
        self.toast(_("« {name} » mis à la corbeille").format(name=name),
                   undo=lambda: self._undo_trash(entry, unpinned))

    def _undo_trash(self, entry: TrashEntry, unpinned: list[Path]):
        try:
            restored = self.store.restore(entry)
        except StorageError as e:
            self.toast(str(e))
            self.sidebar.refresh_trash()
            return
        for pin in unpinned:   # rétablit aussi les épingles (un dossier peut en contenir)
            self.pins.pin(restored / pin.relative_to(entry.original))
        self.sidebar.refresh(select=restored)
        self._toast_restored(restored)

    def _toast_restored(self, restored: Path):
        name = restored.name if restored.is_dir() else restored.stem
        self.toast(_("« {name} » restauré").format(name=name))

    def _show_trash(self, show: bool):
        """Bascule la barre latérale entre l'arborescence et la corbeille."""
        self.lookup_action("show-trash").set_state(GLib.Variant.new_boolean(show))
        self.sidebar.show_trash(show)

    def _on_trash_changed(self, non_empty: bool):
        action = self.lookup_action("empty-trash")
        if action is not None:
            action.set_enabled(non_empty)

    def _trash_entry(self, trashed: Path) -> TrashEntry | None:
        for entry in self.store.list_trash():
            if entry.trashed == trashed:
                return entry
        self.toast(_("« {name} » n'est plus dans la corbeille.").format(name=trashed.name))
        self.sidebar.refresh_trash()
        return None

    def _restore_from_trash(self, trashed: Path):
        entry = self._trash_entry(trashed)
        if entry is None:
            return
        try:
            restored = self.store.restore(entry)
        except StorageError as e:
            self.toast(str(e))
            self.sidebar.refresh_trash()
            return
        self.sidebar.refresh(select=restored)
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
            self.sidebar.refresh_trash()

        confirm(self,
                heading=_("Supprimer « {name} » définitivement ?").format(name=entry.title),
                body=_("Cette action est irréversible."),
                ok_label=_("Supprimer"),
                on_ok=do_delete)

    def _empty_trash(self):
        count = len(self.store.list_trash())
        if not count:
            return

        def do_empty():
            try:
                self.store.empty_trash()
            except StorageError as e:
                self.toast(str(e))
            else:
                self.toast(_("Corbeille vidée"))
            self.sidebar.refresh_trash()

        confirm(self,
                heading=_("Vider la corbeille ?"),
                body=(_("1 élément sera supprimé définitivement.") if count == 1
                      else _("{count} éléments seront supprimés définitivement.").format(count=count))
                     + " " + _("Cette action est irréversible."),
                ok_label=_("Vider la corbeille"),
                on_ok=do_empty)

    # ================================================================== Suivi des chemins

    def _path_moved(self, old: Path, new: Path):
        """À appeler après tout renommage / déplacement : onglets et épingles suivent."""
        for page in self.tabs.pages():
            page.remap(old, new)
        self.pins.remap(old, new)
        self.tabs.sync_titles()
        page = self.current_page
        self.window_title.set_title(page.title if page is not None else "Lumina")
        self.sidebar.set_current(self.current_path)

    @staticmethod
    def _remap_paths(paths: set[Path], old: Path, new: Path) -> set[Path]:
        """Transpose les chemins situés sous `old` vers `new` (les autres sont conservés)."""
        out = set()
        for p in paths:
            try:
                out.add(new / p.relative_to(old))
            except ValueError:
                out.add(p)
        return out

    # ================================================================== Onglets et éditeur

    def open_note(self, path: Path, new_tab: bool = False):
        """
        Ouvre `path` dans l'onglet actif (ou dans un nouvel onglet, forcément s'il
        n'y en a aucun : l'état vide laisse alors place à l'éditeur). Une note déjà
        ouverte dans un onglet n'est pas dupliquée : on bascule simplement dessus.
        """
        existing = self.tabs.find(path)
        if existing is not None:
            self.tabs.select(existing)
            self._focus_editor()
            return
        if new_tab or self.current_page is None:
            page = self._make_page()
            if page.load(path):
                self.tabs.add(page)   # la sélection appelle _on_tab_selected
            return
        page = self.current_page
        if page.load(path):   # la note précédente de l'onglet est d'abord enregistrée
            self._on_tab_selected(page)

    def _new_tab(self):
        """Ctrl+T : nouvel onglet avec une page blanche."""
        page = self._make_page()
        page.start_draft(self._folder_of(self._keyboard_target()))
        self._show_trash(False)
        self.tabs.add(page)

    def _on_tab_selected(self, page: NotePage | None):
        """Onglet actif changé (ou son contenu) : titre, barre latérale, focus.
        `page` vaut None quand le dernier onglet a été fermé (état vide)."""
        self.tabs.sync_titles()
        self.window_title.set_title(page.title if page is not None else "Lumina")
        self.sidebar.set_current(page.path if page is not None else None)
        self._focus_editor()

    def _materialize_draft(self, page: NotePage) -> bool:
        """Première frappe dans une page blanche : crée sa note sur le disque."""
        folder = page.draft_parent if page.draft_parent.is_dir() else self.store.root
        try:
            page.path = self.store.create_note(folder, _("Sans titre"))
        except StorageError as e:
            self.toast(str(e))
            return False
        self.tabs.sync_titles()
        if page is self.current_page:
            self.window_title.set_title(page.title)
        self.sidebar.refresh(select=page.path)
        self.sidebar.set_current(self.current_path)
        return True

    def _focus_editor(self):
        page = self.current_page
        if page is None:   # état vide : rien à éditer, la barre latérale reste accessible
            return
        # set_focus fonctionne aussi avant l'affichage de la fenêtre (au lancement).
        self.set_focus(page.text_view)
        if self.split_view.get_collapsed():   # mode étroit : on révèle l'éditeur
            self.split_view.set_show_sidebar(False)

    def _flush(self):
        """Enregistre immédiatement tous les onglets modifiés."""
        for page in self.tabs.pages():
            page.flush()

    def _print(self):
        page = self.current_page
        if page is not None:
            print_note(self, page.title, page.text(), on_error=self.toast)

    def _on_active_changed(self, *_args):
        if self.is_active():
            self.sidebar.refresh()

    def _on_close_request(self, _window):
        self._flush()
        # Mémorise les onglets ouverts : ils seront rouverts au prochain lancement.
        self.get_application().save_session(*self.tabs.session())
        return False   # laisse la fenêtre se fermer
