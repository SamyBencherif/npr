#!/usr/bin/env python3
"""NPR text-mode terminal reader — https://text.npr.org/"""

import curses
import textwrap
import sys
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://text.npr.org"
HEADERS = {"User-Agent": "npr-terminal/1.0"}


@dataclass
class Article:
    title: str
    url: str


@dataclass
class ArticleContent:
    title: str
    author: str
    date: str
    sections: list[tuple[str, list[str]]] = field(default_factory=list)  # (heading, paragraphs)


def fetch(path: str) -> BeautifulSoup:
    url = path if path.startswith("http") else BASE_URL + path
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def get_headlines() -> list[Article]:
    soup = fetch("/")
    articles = []
    for li in soup.select("ul li a"):
        href = li.get("href", "")
        title = li.get_text(strip=True)
        if href.startswith("/nx-") and title:
            articles.append(Article(title=title, url=href))
    return articles


_DAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
_SKIP_PREFIXES = ("Text-Only Version", "NPR>", "NPR >", "Go To Full Site", "Heard on")


def _should_skip(text: str) -> bool:
    if not text or len(text) < 4:
        return True
    for prefix in _SKIP_PREFIXES:
        if text.startswith(prefix) or prefix in text[:30]:
            return True
    return False


def get_article(path: str) -> ArticleContent:
    soup = fetch(path)

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else ""
    if not title:
        t = soup.find("title")
        title = t.get_text(strip=True) if t else "Untitled"

    author = ""
    date = ""

    # h1 text to deduplicate section headings that use <strong> inside <p>
    seen_headings: set[str] = set()
    if title:
        seen_headings.add(title)

    sections: list[tuple[str, list[str]]] = []
    current_heading = ""
    current_paras: list[str] = []
    found_title = False

    body = soup.find("body")
    if not body:
        return ArticleContent(title=title, author=author, date=date, sections=sections)

    for tag in body.find_all(["h1", "h2", "h3", "p"]):
        text = tag.get_text(strip=True)

        if tag.name == "h1":
            found_title = True
            seen_headings.add(text)
            continue

        if not found_title:
            continue

        if tag.name in ("h2", "h3"):
            if text in seen_headings:
                continue
            seen_headings.add(text)
            if current_paras:
                sections.append((current_heading, current_paras))
            current_heading = text
            current_paras = []
            continue

        # <p> tag
        if _should_skip(text):
            continue
        if not author and text.startswith("By "):
            author = text
            continue
        if not date and any(d in text for d in _DAYS) and len(text) < 80:
            date = text
            continue
        # skip lines that duplicate a section heading (sometimes wrapped in <p><strong>)
        if text in seen_headings:
            continue
        if len(text) >= 20:
            current_paras.append(text)

    if current_paras:
        sections.append((current_heading, current_paras))

    return ArticleContent(title=title, author=author, date=date, sections=sections)


# ── drawing helpers ──────────────────────────────────────────────────────────

def draw_bar(win, y: int, text: str, attr=curses.A_REVERSE):
    h, w = win.getmaxyx()
    line = text.ljust(w)[:w]
    try:
        win.addstr(y, 0, line, attr)
    except curses.error:
        pass


def draw_text(win, y: int, x: int, text: str, attr=0) -> int:
    """Draw text, return new y after wrapping."""
    h, w = win.getmaxyx()
    max_w = w - x - 1
    if max_w < 1:
        return y
    lines = textwrap.wrap(text, max_w) or [""]
    for line in lines:
        if y >= h - 1:
            break
        try:
            win.addstr(y, x, line, attr)
        except curses.error:
            pass
        y += 1
    return y


# ── headline list view ───────────────────────────────────────────────────────

def run_list(stdscr, articles: list[Article]) -> Optional[str]:
    """Show scrollable headline list. Returns article URL or None to quit."""
    curses.curs_set(0)
    curses.use_default_colors()
    try:
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
    except curses.error:
        pass

    sel = 0
    offset = 0

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        visible = h - 3  # header + footer

        # clamp
        if sel < 0:
            sel = 0
        if sel >= len(articles):
            sel = len(articles) - 1
        if sel < offset:
            offset = sel
        if sel >= offset + visible:
            offset = sel - visible + 1

        draw_bar(stdscr, 0, "  NPR News  ·  text.npr.org  ·  ↑↓ navigate  ·  Enter open  ·  q quit")

        for i, art in enumerate(articles[offset: offset + visible]):
            idx = offset + i
            y = i + 1
            marker = "▶ " if idx == sel else "  "
            num = f"{idx + 1:2}. "
            title = art.title
            line = (marker + num + title)[: w - 1]
            attr = curses.color_pair(2) if idx == sel else 0
            try:
                stdscr.addstr(y, 0, line.ljust(w - 1)[:w - 1], attr)
            except curses.error:
                pass

        draw_bar(stdscr, h - 1, f"  {len(articles)} stories  ·  {sel + 1}/{len(articles)}")
        stdscr.refresh()

        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            return None
        elif key in (curses.KEY_UP, ord("k")):
            sel -= 1
        elif key in (curses.KEY_DOWN, ord("j")):
            sel += 1
        elif key in (curses.KEY_PPAGE,):
            sel -= visible
        elif key in (curses.KEY_NPAGE,):
            sel += visible
        elif key in (curses.KEY_HOME, ord("g")):
            sel = 0
        elif key in (curses.KEY_END, ord("G")):
            sel = len(articles) - 1
        elif key in (curses.KEY_ENTER, 10, 13):
            return articles[sel].url
        elif key == ord("r"):
            return "RELOAD"


# ── article reader view ──────────────────────────────────────────────────────

def run_article(stdscr, path: str):
    """Fetch and display an article with scrolling."""
    curses.curs_set(0)
    curses.use_default_colors()
    try:
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_GREEN, -1)
    except curses.error:
        pass

    h, w = stdscr.getmaxyx()

    # loading screen
    stdscr.erase()
    draw_bar(stdscr, 0, "  Loading article…")
    stdscr.refresh()

    try:
        art = get_article(path)
    except Exception as e:
        stdscr.erase()
        draw_bar(stdscr, 0, "  Error")
        stdscr.addstr(2, 2, f"Failed to load: {e}")
        stdscr.addstr(4, 2, "Press any key to go back.")
        stdscr.refresh()
        stdscr.getch()
        return

    # build line buffer
    lines: list[tuple[str, int]] = []  # (text, attr)

    def push(text="", attr=0):
        lines.append((text, attr))

    max_w = min(w - 4, 88)

    push()
    for part in textwrap.wrap(art.title, max_w):
        lines.append((part, curses.A_BOLD | curses.color_pair(3)))
    push()
    if art.author:
        push(art.author, curses.color_pair(4))
    if art.date:
        push(art.date)
    push()
    push("─" * min(max_w, w - 4))
    push()

    for heading, paras in art.sections:
        if heading:
            push()
            for part in textwrap.wrap(heading, max_w):
                lines.append((part, curses.A_BOLD | curses.color_pair(1)))
            push()
        for para in paras:
            for part in textwrap.wrap(para, max_w) or [""]:
                push(part)
            push()

    scroll = 0
    total = len(lines)

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        visible = h - 2

        if scroll < 0:
            scroll = 0
        if scroll > max(0, total - visible):
            scroll = max(0, total - visible)

        draw_bar(stdscr, 0, f"  NPR  ·  {art.title[:w - 30]}  ·  q back")

        for i in range(visible):
            li = scroll + i
            if li >= total:
                break
            text, attr = lines[li]
            try:
                stdscr.addstr(i + 1, 2, text[:w - 3], attr)
            except curses.error:
                pass

        pct = int(100 * (scroll + visible) / total) if total else 100
        pct = min(pct, 100)
        draw_bar(stdscr, h - 1, f"  ↑↓/jk scroll  ·  space/b page  ·  q back  ·  {pct}%")
        stdscr.refresh()

        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), ord("b"), 27):
            return
        elif key in (curses.KEY_UP, ord("k")):
            scroll -= 1
        elif key in (curses.KEY_DOWN, ord("j")):
            scroll += 1
        elif key in (ord(" "), curses.KEY_NPAGE):
            scroll += visible
        elif key in (curses.KEY_PPAGE,):
            scroll -= visible
        elif key in (curses.KEY_HOME, ord("g")):
            scroll = 0
        elif key in (curses.KEY_END, ord("G")):
            scroll = max(0, total - visible)


# ── main loop ────────────────────────────────────────────────────────────────

def loading_screen(stdscr):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    msg = "Fetching headlines from text.npr.org…"
    stdscr.addstr(h // 2, max(0, (w - len(msg)) // 2), msg)
    stdscr.refresh()


def main(stdscr):
    curses.use_default_colors()
    stdscr.keypad(True)

    articles: list[Article] = []

    while True:
        if not articles:
            loading_screen(stdscr)
            try:
                articles = get_headlines()
            except Exception as e:
                stdscr.erase()
                stdscr.addstr(2, 2, f"Error fetching headlines: {e}")
                stdscr.addstr(4, 2, "Press any key to retry, q to quit.")
                stdscr.refresh()
                key = stdscr.getch()
                if key in (ord("q"), ord("Q")):
                    return
                articles = []
                continue

        result = run_list(stdscr, articles)

        if result is None:
            return
        elif result == "RELOAD":
            articles = []
            continue
        else:
            run_article(stdscr, result)


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        sys.exit(0)
