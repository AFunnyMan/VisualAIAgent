"""Visual styling helpers for the Streamlit desktop interface."""

from html import escape

import streamlit as st

_THEME_CSS = """
<style>
:root {
  --vaa-canvas: #F5F6F4;
  --vaa-panel: #FFFFFF;
  --vaa-ink: #20312E;
  --vaa-muted: #697773;
  --vaa-accent: #147D73;
  --vaa-accent-soft: #E7F2F0;
  --vaa-border: #DDE4E1;
  --vaa-shadow: 0 1px 2px rgba(32, 49, 46, 0.04), 0 8px 24px rgba(32, 49, 46, 0.045);
  --vaa-radius: 12px;
}

html, body, [data-testid="stAppViewContainer"], .stApp {
  background: var(--vaa-canvas);
  color: var(--vaa-ink);
}

[data-testid="stHeader"] {
  background: transparent;
  pointer-events: none !important;
}

[data-testid="stToolbar"], [data-testid="stToolbar"] * {
  pointer-events: none !important;
}

[data-testid="stToolbar"]:not(:has([data-testid="stExpandSidebarButton"])) {
  display: none !important;
}

[data-testid="stAppDeployButton"],
[data-testid="stToolbarActions"],
#MainMenu,
[data-testid="stDecoration"] {
  display: none !important;
}

[data-testid="stExpandSidebarButton"] {
  display: flex !important;
  color: var(--vaa-ink);
  pointer-events: auto !important;
}

[data-testid="stExpandSidebarButton"] * { pointer-events: auto !important; }

.stApp:has([data-testid="stExpandSidebarButton"]) [data-testid="stMainBlockContainer"] {
  padding-top: 4rem !important;
}

[data-testid="stMainBlockContainer"] {
  max-width: 1480px;
  padding: 2rem !important;
}

[data-testid="stSidebar"] {
  background: #EEF1EE;
  border-right: 1px solid var(--vaa-border);
  min-width: 196px;
  max-width: 196px;
}

[data-testid="stSidebar"] > div:first-child {
  width: 196px;
}

[data-testid="stSidebarHeader"] {
  min-height: 2.25rem;
  height: 2.25rem;
  padding: 0.35rem 0.75rem;
}

[data-testid="stSidebarContent"] {
  padding-top: 0.25rem;
}

[data-testid="stSidebarUserContent"] {
  min-height: calc(100vh - 2.5rem);
  padding: 0 0.75rem 1rem;
}

[data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] {
  display: flex;
  flex-direction: column;
  min-height: calc(100dvh - 130px);
}

[data-testid="stSidebarUserContent"]
> div
> [data-testid="stVerticalBlock"]
> [data-testid="stLayoutWrapper"]:has(> .st-key-sidebar-footer) {
  margin-top: auto;
}

.st-key-navigation {
  width: 100%;
}

.st-key-navigation [data-testid="stRadioGroup"],
.st-key-navigation label[data-testid="stRadioOption"] {
  width: 100%;
  box-sizing: border-box;
}

[data-testid="stSidebar"] [data-testid="stRadioGroup"] > label {
  color: var(--vaa-muted);
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

[data-testid="stSidebar"] [data-testid="stRadioGroup"][role="radiogroup"] {
  gap: 0.25rem;
}

[data-testid="stSidebar"] label[data-testid="stRadioOption"] {
  position: relative;
  min-height: 2.4rem;
  padding: 0.52rem 0.7rem;
  border: 1px solid transparent;
  border-radius: 9px;
  transition: background-color 120ms ease, border-color 120ms ease;
}

[data-testid="stSidebar"]
label[data-testid="stRadioOption"]
> div
> div
> div:not([data-testid]) {
  display: none;
}

[data-testid="stSidebar"] label[data-testid="stRadioOption"]
[data-testid="stMarkdownContainer"] {
  display: flex;
  align-items: center;
  gap: 0.58rem;
  color: inherit;
  font-size: 0.88rem;
  font-weight: 620;
}

[data-testid="stSidebar"] label[data-testid="stRadioOption"] .material-symbols-rounded {
  color: #60716D;
  font-size: 1.08rem;
}

[data-testid="stSidebar"] label[data-testid="stRadioOption"]:hover {
  background: rgba(231, 242, 240, 0.55);
  border-color: #CFE0DC;
}

[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"] {
  background: var(--vaa-accent-soft);
  border-color: #C4DED8;
  box-shadow: none;
  color: #0F655D;
}

[data-testid="stSidebar"] label[data-testid="stRadioOption"]:has(input:focus-visible) {
  outline: 2px solid var(--vaa-accent);
  outline-offset: 2px;
}

[data-testid="stSidebar"] label[data-testid="stRadioOption"][data-selected="true"]
.material-symbols-rounded {
  color: var(--vaa-accent);
}

h1, h2, h3, h4, h5, h6,
[data-testid="stMarkdownContainer"] p,
[data-testid="stCaptionContainer"] {
  color: var(--vaa-ink);
}

[data-testid="stMainBlockContainer"] h1 {
  font-size: 1.75rem !important;
  line-height: 1.2;
  letter-spacing: -0.035em;
  padding: 0 !important;
}

[data-testid="stMainBlockContainer"] h3 {
  font-size: 1.125rem !important;
  line-height: 1.35;
  letter-spacing: -0.02em;
  padding: 0 !important;
}

h2 { letter-spacing: -0.02em; }

[data-testid="stCaptionContainer"],
small {
  color: var(--vaa-muted) !important;
}

div[data-testid="stButton"] > button,
div[data-testid="stFormSubmitButton"] > button {
  min-height: 2.45rem;
  border-radius: 9px;
  border-color: #C9D5D1;
  font-weight: 650;
  box-shadow: none;
}

div[data-testid="stButton"] > button[kind="primary"],
div[data-testid="stFormSubmitButton"] > button[kind="primary"] {
  background: var(--vaa-accent);
  border-color: var(--vaa-accent);
}

button[kind="primary"] [data-testid="stMarkdownContainer"] p {
  color: #FFFFFF !important;
}

div[data-baseweb="input"] > div,
div[data-baseweb="select"] > div,
div[data-baseweb="textarea"] > div {
  border-color: #CED8D4;
  border-radius: 9px;
  background: #FFFFFF;
}

[data-testid="stMetric"] {
  background: #F8FAF8;
  border: 1px solid var(--vaa-border);
  border-radius: 10px;
  padding: 0.8rem 0.9rem;
}

[data-testid="stAlert"] {
  border-radius: 10px;
  border-width: 1px;
}

[data-testid="stExpander"] {
  background: #FAFBFA;
  border: 1px solid var(--vaa-border);
  border-radius: 10px;
}

.st-key-workspace-header {
  min-height: 64px;
  display: flex;
  align-items: center;
  padding: 0 0 0.75rem;
  border-bottom: 1px solid var(--vaa-border);
  margin-bottom: 0;
}

.st-key-workspace-header [data-testid="stHorizontalBlock"] {
  align-items: center;
}

.st-key-camera-panel,
.st-key-chat-panel,
.st-key-activity-panel,
.st-key-settings-panel {
  background: var(--vaa-panel);
  border: 1px solid var(--vaa-border);
  border-radius: var(--vaa-radius);
  box-shadow: var(--vaa-shadow);
  padding: clamp(0.9rem, 1.5vw, 1.25rem);
}

.st-key-camera-panel,
.st-key-chat-panel {
  min-height: 470px;
}

.st-key-activity-panel {
  margin-top: 1rem;
}

.st-key-settings-panel {
  max-width: 980px;
}

.st-key-workspace-grid
> [data-testid="stLayoutWrapper"]
> [data-testid="stHorizontalBlock"] {
  align-items: stretch;
  gap: 1rem;
}

.st-key-workspace-grid
> [data-testid="stLayoutWrapper"]
> [data-testid="stHorizontalBlock"]
> [data-testid="stColumn"]:first-child {
  flex: 3 1 0 !important;
  width: 60% !important;
}

.st-key-workspace-grid
> [data-testid="stLayoutWrapper"]
> [data-testid="stHorizontalBlock"]
> [data-testid="stColumn"]:last-child {
  flex: 2 1 0 !important;
  width: 40% !important;
}

.st-key-workspace-grid
> [data-testid="stLayoutWrapper"]
> [data-testid="stHorizontalBlock"]
> [data-testid="stColumn"]
> [data-testid="stVerticalBlock"] {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.st-key-workspace-grid
> [data-testid="stLayoutWrapper"]
> [data-testid="stHorizontalBlock"]
> [data-testid="stColumn"]
> [data-testid="stVerticalBlock"]
> [data-testid="stLayoutWrapper"]:has(> .st-key-camera-panel),
.st-key-workspace-grid
> [data-testid="stLayoutWrapper"]
> [data-testid="stHorizontalBlock"]
> [data-testid="stColumn"]
> [data-testid="stVerticalBlock"]
> [data-testid="stLayoutWrapper"]:has(> .st-key-chat-panel) {
  display: flex;
  flex: 1 1 auto;
}

.st-key-camera-panel,
.st-key-chat-panel {
  width: 100%;
  height: 100%;
  box-sizing: border-box;
}

.vaa-brand {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  margin: 0.1rem 0 1.1rem;
}

.vaa-brand__mark {
  position: relative;
  width: 2rem;
  height: 2rem;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  color: var(--vaa-accent);
}

.vaa-brand__mark::before {
  content: "";
  position: absolute;
  inset: 0.32rem;
  border: 2px solid currentColor;
  border-radius: 50%;
}

.vaa-brand__mark::after {
  content: "";
  position: absolute;
  top: 50%;
  left: 50%;
  width: 0.42rem;
  height: 0.42rem;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0.52rem -0.5rem 0 -0.1rem #79B7AF;
  transform: translate(-50%, -50%);
}

.vaa-brand__title {
  color: var(--vaa-ink);
  font-size: 0.95rem;
  font-weight: 750;
  line-height: 1.2;
}

.vaa-brand__subtitle {
  color: var(--vaa-muted);
  font-size: 0.72rem;
  line-height: 1.35;
  margin-top: 0.12rem;
}

.vaa-status {
  display: inline-flex;
  align-items: center;
  gap: 0.42rem;
  width: fit-content;
  padding: 0.36rem 0.62rem;
  border: 1px solid #BFDAD5;
  border-radius: 999px;
  color: #0F655D;
  background: var(--vaa-accent-soft);
  font-size: 0.76rem;
  font-weight: 650;
  line-height: 1;
}

.vaa-status::before {
  content: "";
  width: 0.42rem;
  height: 0.42rem;
  border-radius: 50%;
  background: var(--vaa-accent);
}

.vaa-status--muted {
  color: #63716D;
  background: #F0F2F0;
  border-color: #D7DEDB;
}

.vaa-status--muted::before { background: #8B9793; }

.vaa-status--warning {
  color: #8A5A0A;
  background: #FFF7E7;
  border-color: #ECD8AA;
}

.vaa-status--warning::before { background: #C98A1A; }

.vaa-status--error {
  color: #A03A35;
  background: #FFF0EE;
  border-color: #EDC6C2;
}

.vaa-status--error::before { background: #C6534D; }

.vaa-header-status {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
  align-items: center;
  justify-content: flex-end;
}

[data-testid="stChatMessageAvatarUser"],
[data-testid="stChatMessageAvatarAssistant"],
[data-testid="stChatMessageAvatarCustom"],
[data-testid="stChatMessageAvatar"] {
  color: #FFFFFF;
  background: var(--vaa-accent) !important;
}

.vaa-object-table {
  width: 100%;
  border-spacing: 0;
  border-collapse: separate;
  overflow: hidden;
  border: 1px solid var(--vaa-border);
  border-radius: 10px;
  color: var(--vaa-ink);
  font-size: 0.84rem;
}

.vaa-object-table th,
.vaa-object-table td {
  padding: 0.68rem 0.78rem;
  border-bottom: 1px solid #E8ECEA;
  text-align: left;
  vertical-align: middle;
}

.vaa-object-table th {
  color: var(--vaa-muted);
  background: #F7F9F7;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.035em;
}

.vaa-object-table tr:last-child td { border-bottom: 0; }

.vaa-activity-row {
  display: grid;
  grid-template-columns: minmax(4.5rem, auto) 1fr;
  gap: 0.85rem;
  align-items: start;
  padding: 0.72rem 0;
  border-bottom: 1px solid #E8ECEA;
}

.vaa-activity-row:last-child { border-bottom: 0; }

.vaa-activity-time {
  color: var(--vaa-muted);
  font-size: 0.76rem;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.vaa-activity-text {
  color: var(--vaa-ink);
  font-size: 0.86rem;
  line-height: 1.45;
}

.vaa-preview-empty {
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  aspect-ratio: 16 / 9;
  min-height: 210px;
  display: grid;
  place-items: center;
  padding: 1.5rem;
  border: 1px dashed #CDD7D3;
  border-radius: 10px;
  color: var(--vaa-muted);
  background: #F0F2F0;
  text-align: center;
}

.vaa-chat-empty {
  min-height: 250px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.38rem;
  padding: 1.5rem;
  color: var(--vaa-muted);
  text-align: center;
}

.vaa-chat-empty strong {
  color: var(--vaa-ink);
  font-size: 0.96rem;
  font-weight: 700;
}

.vaa-chat-empty span {
  max-width: 26rem;
  font-size: 0.8rem;
  line-height: 1.55;
}

.st-key-sidebar-footer {
  margin-top: auto !important;
  padding-top: 1rem;
  color: var(--vaa-muted);
  font-size: 0.74rem;
}

@media (max-width: 1000px) {
  [data-testid="stMainBlockContainer"] {
    padding: 1rem !important;
  }

  .st-key-workspace-grid
  > [data-testid="stLayoutWrapper"]
  > [data-testid="stHorizontalBlock"] {
    flex-direction: column;
  }

  .st-key-workspace-grid
  > [data-testid="stLayoutWrapper"]
  > [data-testid="stHorizontalBlock"]
  > [data-testid="stColumn"]:nth-child(n) {
    flex: 1 1 auto !important;
    width: 100% !important;
    min-width: 0 !important;
  }

  .st-key-camera-panel,
  .st-key-chat-panel {
    min-height: auto;
  }
}

@media (max-width: 640px) {
  [data-testid="stSidebar"] {
    min-width: min(86vw, 260px);
    max-width: min(86vw, 260px);
  }

  [data-testid="stSidebar"] > div:first-child {
    width: min(86vw, 260px);
  }

  .st-key-camera-panel,
  .st-key-chat-panel,
  .st-key-activity-panel,
  .st-key-settings-panel {
    padding: 0.8rem;
  }
}
</style>
"""


def inject_theme() -> None:
    """Inject the local CSS theme without external assets or network requests."""

    st.html(_THEME_CSS)


def brand_html(title: str = "视觉记忆", subtitle: str = "VisualAIAgent") -> str:
    """Return an escaped compact brand lockup for a sidebar or header."""

    return (
        '<div class="vaa-brand">'
        '<div class="vaa-brand__mark" aria-hidden="true"></div>'
        '<div><div class="vaa-brand__title">'
        f"{escape(title)}"
        '</div><div class="vaa-brand__subtitle">'
        f"{escape(subtitle)}"
        "</div></div></div>"
    )


def status_html(label: str, tone: str = "accent") -> str:
    """Return an escaped status badge; tone may be accent, muted, warning, or error."""

    safe_tone = tone if tone in {"muted", "warning", "error"} else "accent"
    modifier = "" if safe_tone == "accent" else f" vaa-status--{safe_tone}"
    return f'<span class="vaa-status{modifier}">{escape(label)}</span>'


__all__ = ["brand_html", "inject_theme", "status_html"]
