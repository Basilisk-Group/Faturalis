# Faturalis website

Marketing site plus client login for qr-bench, branded as "Faturalis". Plain HTML/CSS/JS,
no build step, no framework, matching qr-bench's own frontend approach. This site never
talks to a database or the qr-bench API itself. It's a static shell that ends with a login
button pointing at wherever qr-bench is actually running.

The look (warm paper background, serif headings, amber accent) is its own design, drawn
from the actual Faturalis logo ink color rather than copied from any other project.

Two pages:

- `index.html`: the marketing home page (hero, functionalities, how it works, CTA).
- `login.html`: a demo login gate, not real authentication. qr-bench has no user accounts
  of its own, so any email/password is accepted; submitting just redirects the browser to
  the configured qr-bench URL. This is intentional, not a stub left unfinished.

## How to test it locally

You need two things running at once: qr-bench itself, and this static site in front of it.

1. Start qr-bench (from the `qr-bench/` folder, see its own README for full setup):

   ```bash
   cd ../qr-bench
   uv run uvicorn qr_bench.main:app --reload
   ```

   This serves the actual product at `http://127.0.0.1:8000/`.

2. In another terminal, serve this folder as static files. Any static server works, e.g.:

   ```bash
   cd website
   python3 -m http.server 8123
   ```

   Then open `http://127.0.0.1:8123/`.

3. Click "Entrar" (top-right nav, or the hero/CTA buttons) to reach the login page, type
   anything into the email/password fields, and submit. It redirects straight to the
   qr-bench app started in step 1.

There's no build or install step for the website itself, it's just static files.

## Pointing the login at a different qr-bench instance

The target URL lives in `assets/config.js` (`DEFAULT_APP_URL`, currently
`http://127.0.0.1:8000/`). Two ways to change it:

- Edit the file: change `DEFAULT_APP_URL` to wherever qr-bench is hosted, e.g.
  `https://qr-bench.example.com/`.
- Query param, no edit needed: open the site once with `?app=<url>`, e.g.
  `http://127.0.0.1:8123/login.html?app=http://192.168.1.50:8000/`. The value is saved in
  the visitor's browser (`localStorage`) and reused on every later visit from that browser.

## Assets

`assets/logo.png` / `assets/logo-name.png` are the source marks (copied from `../logos/`).
`assets/favicon-*.png` are square, transparent-background crops generated from `logo.png`:
`favicon-32.png` / `favicon-192.png` are a white cut of the mark, used for the browser tab
so it stays visible against Chrome's dark tab bar; `favicon-512.png` is the original ink
colored cut, used inline as the small logo mark in the nav bar, footer, and login card. If
the source logo ever changes, regenerate all four from it rather than editing the PNGs by
hand.

## Notes

- Content is PT-PT only, matching qr-bench's own flag/export copy (see the qr-bench
  `CLAUDE.md` for why that text is deliberately not bilingual). No language toggle here.
- Nothing in this folder is wired into qr-bench's `uv run pytest` suite, and it doesn't need
  to be: it's a static frontend with no logic worth unit-testing beyond "does the redirect
  work," which is a one-click manual check (see above).
