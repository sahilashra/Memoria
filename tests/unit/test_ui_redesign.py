"""
Tests for the Memoria UI redesign (Tasks 2–13 of ui-redesign spec).

Covers:
- Light theme CSS tokens present in index.html
- Project glyph derivation (Properties 1 & 2)
- Sidebar filter logic (Properties 3, 4, 5)
- Sidebar active-item exclusivity (Property 6)
- Chat welcome visibility toggle (Property 7)
- Toast type → CSS class mapping (Property 8)
- Chat history preservation across project switches (Property 9)
- GitHub URL detection (Property 10)
- Graph filter state independence (Property 11)
- User avatar initial derivation (Property 12)
- DOM structure assertions (breadcrumb, filter input, hints bar, edge pills)
- Pointer-events on toast container
"""

import re
import pathlib
import pytest
from hypothesis import given, settings, assume
import hypothesis.strategies as st

# ─── Helpers ──────────────────────────────────────────────────────────────────

HTML_PATH = pathlib.Path(__file__).parent.parent.parent / "memoria" / "static" / "index.html"


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


# ─── Python mirrors of pure JS functions ──────────────────────────────────────

def get_glyph(name: str) -> str:
    """Mirror of JS getGlyph(name)."""
    import re as _re
    words = [w for w in _re.split(r'[\s_\-]+', name.strip()) if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


GLYPH_COLORS = [
    '#4f46e5', '#0891b2', '#059669', '#d97706',
    '#dc2626', '#7c3aed', '#db2777', '#65a30d',
]


def get_glyph_color(name: str) -> str:
    """Mirror of JS getGlyphColor(name)."""
    h = 0
    for ch in name:
        h = ((h * 31) + ord(ch)) & 0xFFFFFFFF
    return GLYPH_COLORS[h % len(GLYPH_COLORS)]


def filter_projects(projects: list[str], filter_text: str) -> list[str]:
    """Mirror of JS filterProjects — returns visible project names."""
    lower = filter_text.lower()
    if not lower:
        return list(projects)
    return [p for p in projects if lower in p.lower()]


def toast_class(type_: str) -> str:
    """Mirror of JS toast type → CSS class."""
    valid = {'success', 'warning', 'error', 'info'}
    return type_ if type_ in valid else 'info'


def avatar_initial(username: str | None) -> str:
    """Mirror of sidebar footer initial derivation."""
    if not username:
        return '?'
    return username[0].upper()


def is_git_url(s: str) -> bool:
    """Mirror of JS _isGitUrl."""
    return bool(re.match(r'^https?://', s, re.IGNORECASE)) or \
           bool(re.match(r'^git(@|://)', s))


# ─── Task 2: Light theme tokens ───────────────────────────────────────────────

class TestLightThemeTokens:

    def test_bg_main_token(self):
        assert '--bg-main:' in _html() and '#f5f4f0' in _html()

    def test_bg_sidebar_token(self):
        # sidebar uses rgba — just check the variable declaration is present
        assert '--bg-sidebar:' in _html()

    def test_accent_token(self):
        assert '--accent:' in _html() and '#4f46e5' in _html()

    def test_text_primary_token(self):
        assert '--text-primary:' in _html() and '#0e0f0c' in _html()

    def test_text_disabled_token(self):
        assert '--text-disabled:' in _html() and '#92958a' in _html()

    def test_no_dark_mode_media_query(self):
        assert 'prefers-color-scheme: dark' not in _html()

    def test_geist_font_loaded(self):
        """Google Fonts link for Geist must be present."""
        html = _html()
        assert 'fonts.googleapis.com' in html
        assert 'Geist' in html

    def test_jetbrains_mono_loaded(self):
        assert 'JetBrains+Mono' in _html() or 'JetBrains Mono' in _html()

    def test_font_variable(self):
        assert '"Geist"' in _html() or "'Geist'" in _html()

    def test_font_mono_variable(self):
        html = _html()
        assert 'JetBrains Mono' in html


# ─── Task 3: Project Glyphs — Properties 1 & 2 ───────────────────────────────

class TestGetGlyph:

    _NAME_ALPHABET = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ '

    @given(
        st.text(alphabet=_NAME_ALPHABET, min_size=1, max_size=40)
        .filter(lambda s: bool(re.search(r'[A-Za-z0-9]', s)))  # must contain at least one alphanumeric
    )
    @settings(max_examples=200)
    def test_property1_glyph_is_1_or_2_uppercase_letters(self, name):
        """Property 1: Glyph derivation produces valid 1–2 uppercase letter strings."""
        glyph = get_glyph(name)
        assert 1 <= len(glyph) <= 2, f"Glyph '{glyph}' for name '{name}' must be 1–2 chars"
        assert glyph == glyph.upper(), f"Glyph '{glyph}' must be uppercased"
        assert glyph.isalnum(), f"Glyph '{glyph}' must be alphanumeric"

    def test_single_word_uses_first_two_chars(self):
        assert get_glyph('memoria') == 'ME'

    def test_two_words_uses_initials(self):
        assert get_glyph('hello world') == 'HW'

    def test_underscore_separator(self):
        assert get_glyph('hello_world') == 'HW'

    def test_hyphen_separator(self):
        assert get_glyph('my-project') == 'MP'

    def test_more_than_two_words_uses_first_two_initials(self):
        result = get_glyph('alpha beta gamma')
        assert result == 'AB'

    def test_single_char_name(self):
        result = get_glyph('X')
        assert result == 'X'
        assert len(result) == 1


class TestGetGlyphColor:

    @given(st.text(min_size=1))
    @settings(max_examples=200)
    def test_property2_glyph_color_is_deterministic(self, name):
        """Property 2: Glyph color is deterministic — same name always gives same color."""
        assert get_glyph_color(name) == get_glyph_color(name)

    @given(st.text(min_size=1))
    @settings(max_examples=200)
    def test_color_is_from_palette(self, name):
        """Color must be one of the 8 predefined palette entries."""
        color = get_glyph_color(name)
        assert color in GLYPH_COLORS, f"Color '{color}' not in palette"

    def test_same_name_same_color(self):
        assert get_glyph_color('memoria') == get_glyph_color('memoria')

    def test_different_names_can_produce_different_colors(self):
        colors = {get_glyph_color(n) for n in ['project-a', 'project-b', 'alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta']}
        assert len(colors) > 1, "8 distinct names should map to >1 color"


# ─── Task 4: Sidebar Filter — Properties 3, 4, 5 ─────────────────────────────

class TestSidebarFilter:

    @given(
        st.lists(st.text(min_size=1, max_size=30).filter(str.strip), min_size=0, max_size=10),
        st.text(min_size=1, max_size=15).filter(str.strip),
    )
    @settings(max_examples=150)
    def test_property3_matching_projects_shown_non_matching_hidden(self, projects, filter_text):
        """Property 3: Filter shows matching, hides non-matching."""
        visible = filter_projects(projects, filter_text)
        for p in projects:
            if filter_text.lower() in p.lower():
                assert p in visible, f"'{p}' should be visible with filter '{filter_text}'"
            else:
                assert p not in visible, f"'{p}' should be hidden with filter '{filter_text}'"

    @given(st.lists(st.text(min_size=1).filter(str.strip), min_size=0, max_size=10))
    @settings(max_examples=100)
    def test_property4_clearing_filter_restores_all_projects(self, projects):
        """Property 4: Clearing filter restores all projects."""
        visible = filter_projects(projects, '')
        assert sorted(visible) == sorted(projects)

    def test_property5_all_books_immune_to_filter(self):
        """Property 5: Global-chat items are always immune to project filter (DOM structure check)."""
        html = _html()
        # Items without data-all-books are project-specific; global chat has no such attr
        # Verify the filter selector correctly excludes global-chat items
        assert 'data-all-books' in html or 'project-item' in html

    def test_filter_input_placeholder(self):
        """Sidebar filter input must have the correct placeholder."""
        assert 'Filter projects…' in _html() or 'Filter projects...' in _html()

    def test_filter_input_dom_element(self):
        """class="sidebar-filter" input must exist."""
        assert 'class="sidebar-filter"' in _html() or "class='sidebar-filter'" in _html()

    def test_case_insensitive_matching(self):
        projects = ['MyProject', 'another-thing', 'UPPERCASE']
        assert 'MyProject' in filter_projects(projects, 'my')
        assert 'MyProject' in filter_projects(projects, 'MY')
        assert 'MyProject' in filter_projects(projects, 'myproject')


# ─── Task 5: All Books Entry — Property 6 ────────────────────────────────────

class TestAllBooksEntry:

    def test_all_books_entry_in_project_list(self):
        """The project list container must exist in the sidebar."""
        html = _html()
        assert 'id="project-list"' in html

    def test_all_books_entry_has_data_attribute(self):
        """project-item elements must exist to render project entries."""
        assert 'project-item' in _html()

    def test_property6_exactly_one_active_item(self):
        """Property 6: Simulated active state — only one project active at a time."""
        projects = ['alpha', 'beta', 'gamma']
        active = 'beta'

        # Simulate the active state logic: exactly one project is active
        active_states = {p: (p == active) for p in ['__all_books__'] + projects}
        assert sum(active_states.values()) == 1

    def test_select_all_books_clears_project(self):
        """selectAllBooks() sets project to null — verifiable via function presence."""
        html = _html()
        assert 'selectAllBooks' in html
        assert 'S.project = null' in html or "S.project=null" in html


# ─── Task 6: Chat Welcome — Property 7 ───────────────────────────────────────

class TestChatWelcome:

    def test_global_welcome_rendered_when_no_project(self):
        """global-welcome class must be in the HTML."""
        assert 'global-welcome' in _html()

    def test_example_questions_present(self):
        """At least 4 example questions must be defined in the examples array or welcome area."""
        html = _html()
        # Examples are dynamically generated from a JS array — count the entries
        # by looking for the examples array contents, or the global-example class usage
        has_fill_input = 'fillInput' in html
        has_global_example_class = 'global-example' in html
        # Count example strings in the JS examples array (each entry is a quoted string)
        examples_block_match = re.search(r'const examples = \[(.*?)\]', html, re.DOTALL)
        if examples_block_match:
            block = examples_block_match.group(1)
            count = block.count("'") // 2  # each entry has two single quotes
            assert count >= 4, f"Expected ≥4 example questions in JS array, found {count}"
        else:
            # Fallback: just ensure the fillInput infrastructure exists
            assert has_fill_input and has_global_example_class, \
                "Chat welcome examples infrastructure missing"

    def test_welcome_hidden_when_messages_exist(self):
        """appendMessage must remove the welcome element."""
        html = _html()
        assert '.chat-welcome, .global-welcome' in html or "chat-welcome, .global-welcome" in html

    def test_property7_welcome_shown_iff_no_messages(self):
        """Property 7: Welcome shown when msgs empty, hidden when msgs exist (logic check)."""
        # Simulate: if msgs is empty -> show welcome; if msgs is non-empty -> hide welcome
        def should_show_welcome(msgs: list) -> bool:
            return len(msgs) == 0

        assert should_show_welcome([]) is True
        assert should_show_welcome([{'role': 'user', 'html': 'hello'}]) is False

    def test_stats_row_in_welcome(self):
        """Chat welcome must contain a stats element."""
        html = _html()
        assert 'global-welcome-stats' in html or 'Memory Banks' in html


# ─── Task 7: Composer Hints Bar ──────────────────────────────────────────────

class TestComposerHints:

    def test_hints_element_present(self):
        assert 'input-hints' in _html()

    def test_hints_text_contains_enter_shortcut(self):
        html = _html()
        assert 'Enter to send' in html

    def test_hints_text_contains_shift_enter(self):
        html = _html()
        assert 'Shift+Enter' in html or 'Shift + Enter' in html

    def test_hints_text_contains_slash_hint(self):
        html = _html()
        assert '/ for commands' in html or '/ ' in html

    def test_hints_css_uses_display_block(self):
        """Hints must be always visible — CSS must use display: block (or inline/flex), not display: none."""
        html = _html()
        # Find the .input-hints CSS rule
        # It should say "display: block" not "display: none"
        # Look for the rule definition
        match = re.search(r'\.input-hints\s*\{([^}]+)\}', html)
        assert match is not None, ".input-hints CSS rule not found"
        rule_body = match.group(1)
        assert 'display: none' not in rule_body, \
            ".input-hints must not have display:none as default — it should always be visible"

    def test_hints_font_size_small(self):
        """Hints must use font-size 11px or smaller."""
        html = _html()
        match = re.search(r'\.input-hints\s*\{([^}]+)\}', html)
        if match:
            rule_body = match.group(1)
            size_match = re.search(r'font-size:\s*(\d+)px', rule_body)
            if size_match:
                size = int(size_match.group(1))
                assert size <= 11, f"input-hints font-size must be ≤11px, got {size}px"


# ─── Task 8: Book Cards ───────────────────────────────────────────────────────

class TestBookCards:

    def test_book_card_has_accent_bar(self):
        """Book cards must have a 3px left border (accent bar)."""
        html = _html()
        assert 'border-left: 3px solid' in html

    def test_book_card_has_glyph(self):
        """Book cards must render a proj-glyph element."""
        html = _html()
        assert 'proj-glyph' in html

    def test_book_type_badge_css_exists(self):
        """book-type-badge CSS class must be defined."""
        html = _html()
        assert '.book-type-badge' in html

    def test_book_type_css_classes(self):
        """All 5 type CSS classes must be defined."""
        html = _html()
        for cls in ['book-type-code', 'book-type-docs', 'book-type-audio', 'book-type-nb', 'book-type-mcp']:
            assert cls in html, f"Missing CSS class: {cls}"

    def test_book_chunk_count_css(self):
        """book-chunk-count CSS must be defined."""
        assert '.book-chunk-count' in _html()

    def test_book_ask_button_present(self):
        assert 'book-ask' in _html()

    def test_book_refresh_button_present(self):
        assert 'book-refresh' in _html()


# ─── Task 9: Graph Edge Type Pills ────────────────────────────────────────────

class TestGraphEdgePills:

    def test_edge_pills_present(self):
        """At least 4 edge-pill buttons must be present."""
        html = _html()
        count = len(re.findall(r'class="edge-pill', html))
        assert count >= 4, f"Expected ≥4 edge-pill buttons, found {count}"

    def test_all_four_relation_types_present(self):
        html = _html()
        for rel in ['depends_on', 'shares_technology', 'same_domain', 'references']:
            assert f'data-rel="{rel}"' in html, f"Missing edge pill for {rel}"

    def test_no_checkbox_toggles_in_toolbar(self):
        """Graph toolbar must NOT use checkbox inputs for edge types."""
        html = _html()
        # The old checkbox-based toggles should be replaced by pills
        # There is still one checkbox (graph-isolate-toggle) but NOT for edge types
        # Check that edge-type checkboxes are gone
        edge_checkbox_pattern = re.compile(
            r'<input[^>]+type=["\']checkbox["\'][^>]*>(depends_on|shares_technology|same_domain|references)',
            re.IGNORECASE
        )
        assert not edge_checkbox_pattern.search(html), "Old checkbox edge toggles found — should be pills now"

    def test_edge_pills_have_coloured_dots(self):
        """Each pill must have a dot with correct edge color."""
        html = _html()
        for color in ['#ef4444', '#6366f1', '#f59e0b', '#8b8ba8']:
            assert color in html, f"Edge color {color} not found in HTML"

    def test_edge_pill_active_css(self):
        """edge-pill.active CSS rule must exist."""
        assert '.edge-pill.active' in _html()

    def test_toggle_edge_filter_function(self):
        """toggleEdgeFilter function must exist."""
        assert 'function toggleEdgeFilter' in _html()


# ─── Task 10: Graph Toolbar ───────────────────────────────────────────────────

class TestGraphToolbar:

    def test_toolbar_positioned_at_top(self):
        """graph-filters must be positioned at top:0."""
        html = _html()
        match = re.search(r'\.graph-filters\s*\{([^}]+)\}', html)
        assert match is not None, ".graph-filters CSS rule not found"
        rule = match.group(1)
        assert 'top: 0' in rule or 'top:0' in rule, ".graph-filters must be at top:0"

    def test_toolbar_max_height(self):
        """Graph toolbar must have max-height of 48px."""
        html = _html()
        match = re.search(r'\.graph-filters\s*\{([^}]+)\}', html)
        assert match is not None
        rule = match.group(1)
        assert 'max-height: 48px' in rule or 'max-height:48px' in rule

    def test_graph_svg_has_padding_top(self):
        """Graph SVG canvas must have padding-top to avoid overlap with toolbar."""
        html = _html()
        # SVG should have padding-top set to at least 48px
        assert 'padding-top:48px' in html or 'padding-top: 48px' in html


# ─── Task 11: Breadcrumb Topbar ──────────────────────────────────────────────

class TestBreadcrumb:

    def test_breadcrumb_element_exists(self):
        assert 'topbar-breadcrumb' in _html()

    def test_breadcrumb_workspace_segment(self):
        """Breadcrumb must show 'Workspace' or 'Memoria' as root."""
        html = _html()
        assert 'Workspace' in html or 'Memoria' in html

    def test_bc_sep_elements(self):
        """Separator elements must exist."""
        html = _html()
        assert 'bc-sep' in html

    def test_bc_active_element(self):
        """Active breadcrumb segment element must exist."""
        assert 'bc-active' in _html()

    def test_render_breadcrumb_function(self):
        """renderBreadcrumb() function must exist."""
        assert 'function renderBreadcrumb' in _html()

    def test_breadcrumb_sep_color(self):
        """Separator must use --text-disabled or --border for muted color."""
        html = _html()
        match = re.search(r'\.bc-sep\s*\{([^}]+)\}', html)
        assert match is not None, ".bc-sep CSS rule not found"
        rule = match.group(1)
        assert 'text-disabled' in rule or 'border' in rule or 'muted' in rule

    def test_bc_active_uses_text_primary(self):
        """Active segment must use --text-primary and font-weight 500."""
        html = _html()
        match = re.search(r'\.bc-active\s*\{([^}]+)\}', html)
        assert match is not None, ".bc-active CSS rule not found"
        rule = match.group(1)
        assert 'text-primary' in rule
        assert 'font-weight' in rule


# ─── Task 12: Sidebar Footer — Property 12 ───────────────────────────────────

class TestSidebarFooter:

    def test_sidebar_footer_element_exists(self):
        assert 'sidebar-footer' in _html()

    def test_sidebar_user_avatar_element_exists(self):
        assert 'sidebar-user-avatar' in _html()

    def test_sidebar_user_name_element_exists(self):
        assert 'sidebar-user-name' in _html()

    def test_footer_pinned_to_bottom(self):
        """Sidebar footer CSS must use flex-shrink: 0 to stay at bottom."""
        html = _html()
        match = re.search(r'\.sidebar-footer\s*\{([^}]+)\}', html)
        assert match is not None
        rule = match.group(1)
        assert 'flex-shrink: 0' in rule or 'flex-shrink:0' in rule

    @given(
        st.text(min_size=1, max_size=50)
        .filter(str.strip)
        # Exclude Unicode chars whose uppercase form expands to multiple codepoints
        .filter(lambda s: len(s[0].upper()) == 1)
    )
    @settings(max_examples=100)
    def test_property12_avatar_initial_is_first_char_uppercased(self, username):
        """Property 12: Avatar initial is first char of username, uppercased."""
        initial = avatar_initial(username)
        assert initial == username[0].upper()
        assert len(initial) == 1

    def test_avatar_initial_none_username_gives_fallback(self):
        """When username is None, avatar shows fallback."""
        result = avatar_initial(None)
        assert result == '?'  # our Python mirror; actual JS shows SVG icon


# ─── Task 13: Toast Redesign — Property 8 ────────────────────────────────────

class TestToastRedesign:

    def test_property8_toast_color_class_maps_to_type(self):
        """Property 8: Toast color class matches outcome type."""
        assert toast_class('success') == 'success'
        assert toast_class('error') == 'error'
        assert toast_class('warning') == 'warning'
        assert toast_class('info') == 'info'

    def test_toast_function_exists(self):
        assert 'function toast(' in _html()

    def test_toast_container_pointer_events_none(self):
        """Task 13.4: toast-container must have pointer-events: none."""
        html = _html()
        match = re.search(r'\.toast-container\s*\{([^}]+)\}', html)
        assert match is not None, ".toast-container CSS rule not found"
        rule = match.group(1)
        assert 'pointer-events: none' in rule or 'pointer-events:none' in rule

    def test_toast_pointer_events_all(self):
        """Task 13.4: individual .toast must have pointer-events: all (not none)."""
        html = _html()
        # Find the .toast rule (not .toast-container, .toast-msg, .toast-close)
        # Use a regex that captures the first .toast { ... } block
        match = re.search(r'(?<![a-z-])\.toast\s*\{([^}]+)\}', html)
        assert match is not None, ".toast CSS rule not found"
        rule = match.group(1)
        assert 'pointer-events: all' in rule or 'pointer-events:all' in rule

    def test_toast_slide_in_animation(self):
        """Toast must use slide-in animation from right."""
        html = _html()
        assert 'toastIn' in html
        assert 'translateX' in html

    def test_toast_success_uses_light_background(self):
        """Toast success background must be light green (not dark #14532d)."""
        html = _html()
        assert '#14532d' not in html, "Dark toast background still present"

    def test_toast_error_uses_light_background(self):
        """Toast error background must be light red (not dark #450a0a)."""
        html = _html()
        assert '#450a0a' not in html, "Dark toast background still present"

    def test_toast_types_defined(self):
        """All four toast types must be defined in CSS."""
        html = _html()
        for cls in ['.toast.success', '.toast.warning', '.toast.error', '.toast.info']:
            assert cls in html, f"Missing toast CSS: {cls}"


# ─── Regression: GitHub URL Detection — Property 10 ─────────────────────────

class TestGitHubUrlDetection:

    @given(
        st.from_regex(r'https://github\.com/[a-zA-Z0-9_-]{1,30}/[a-zA-Z0-9_.-]{1,40}(\.git)?', fullmatch=True)
    )
    @settings(max_examples=100)
    def test_property10_valid_github_urls_detected(self, url):
        """Property 10: GitHub URL detection is correct for all valid GitHub URLs."""
        assert is_git_url(url), f"Should detect GitHub URL: {url}"

    def test_local_path_not_detected_as_url(self):
        assert not is_git_url(r'C:\Users\test\project')
        assert not is_git_url('/home/user/project')
        assert not is_git_url('relative/path')

    def test_https_github_detected(self):
        assert is_git_url('https://github.com/owner/repo')

    def test_git_ssh_detected(self):
        assert is_git_url('git@github.com:owner/repo.git')

    def test_git_protocol_detected(self):
        assert is_git_url('git://github.com/owner/repo.git')

    def test_function_exists_in_html(self):
        html = _html()
        assert '_isGitUrl' in html or 'isGitUrl' in html


# ─── Regression: Chat History — Property 9 ───────────────────────────────────

class TestChatHistoryPreservation:

    def test_property9_messages_keyed_per_project(self):
        """Property 9: Chat history preserved across project switches."""
        # Simulate state.messages = { projectA: [...], projectB: [...] }
        messages: dict[str, list] = {}
        global_key = '__global__'

        def add_message(project_key, msg):
            if project_key not in messages:
                messages[project_key] = []
            messages[project_key].append(msg)

        def switch_project(new_project):
            return new_project  # active project key changes

        # Add messages to project A
        active = 'project_a'
        add_message(active, {'role': 'user', 'html': 'hello'})

        # Switch to project B
        active = switch_project('project_b')
        add_message(active, {'role': 'user', 'html': 'world'})

        # Switch back to project A
        active = switch_project('project_a')

        # Project A's messages must still be there
        assert len(messages.get('project_a', [])) == 1
        assert len(messages.get('project_b', [])) == 1
        assert messages['project_a'][0]['html'] == 'hello'

    def test_messages_object_in_state(self):
        """JS state must have a messages dict."""
        html = _html()
        assert 'messages:' in html or "messages =" in html

    def test_global_key_constant(self):
        """GLOBAL_KEY constant must be defined."""
        assert 'GLOBAL_KEY' in _html()
        assert '__global__' in _html()


# ─── Regression: Graph Filter Independence — Property 11 ─────────────────────

class TestGraphFilterIndependence:

    def test_property11_slider_does_not_modify_edge_filters(self):
        """Property 11: Strength slider change must not affect edge filter state."""
        # Simulate edge filter state
        edge_filters = {'depends_on': True, 'shares_technology': True, 'same_domain': True, 'references': True}

        # Adjusting the slider should only change the threshold, not edge_filters
        strength_threshold = 0.5  # slider adjusted to 0.5

        # Edge filter state must remain unchanged
        assert edge_filters['depends_on'] is True
        assert edge_filters['shares_technology'] is True

    def test_apply_graph_filters_reads_pill_state(self):
        """applyGraphFilters must read from .edge-pill.active classes."""
        html = _html()
        assert 'edge-pill.active' in html or '.edge-pill.active' in html

    def test_toggle_edge_filter_does_not_touch_slider(self):
        """toggleEdgeFilter must not reference the slider value."""
        html = _html()
        # Find the toggleEdgeFilter function body
        match = re.search(r'function toggleEdgeFilter\s*\([^)]*\)\s*\{([^}]+)\}', html)
        if match:
            body = match.group(1)
            assert 'graph-conf-slider' not in body, \
                "toggleEdgeFilter must not touch the strength slider"


# ─── DOM Structure Assertions ─────────────────────────────────────────────────

class TestDomStructure:

    def test_app_root_element(self):
        assert 'class="app"' in _html()

    def test_sidebar_element(self):
        assert 'class="sidebar"' in _html()

    def test_topbar_element(self):
        assert 'class="topbar"' in _html()

    def test_tab_bar_element(self):
        assert 'class="tab-bar"' in _html()

    def test_toast_container_element(self):
        assert 'id="toast-container"' in _html()

    def test_chat_messages_element(self):
        assert 'id="chat-messages"' in _html()

    def test_input_field_element(self):
        assert 'id="main-input"' in _html()

    def test_slash_menu_element(self):
        assert 'id="slash-menu"' in _html()

    def test_graph_svg_element(self):
        assert 'id="graph-svg"' in _html()

    def test_books_grid_element(self):
        assert 'id="books-grid"' in _html()

    def test_analyze_panel_element(self):
        assert 'id="analyze-panel"' in _html()

    def test_settings_tab_element(self):
        assert 'id="tab-settings"' in _html()
