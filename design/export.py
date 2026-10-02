#!/usr/bin/env python3
"""Writes the MaximizePM brand files into this folder: the mark and the logo as SVG, the PNG sizes, the favicon
pictures, favicon.ico, apple-touch-icon.png and MaximizePM.icns. Python stdlib only. The PNG files need a Chrome on
the computer (it draws each SVG into a canvas of the real size); the .icns needs iconutil (macOS).
Run outside a sandbox: python3 design/export.py

The mark is an octopus in a 32 x 32 box: a long mantle that tilts back to the upper left, two sly eyes with a
slanted lid, and eight relaxed arms that are thick at the body and thin at the tips. Deep red on a navy tile, gold
eyes. It is one drawing with three levels: "large" (48 px and larger: thin arm tips, a row of suckers on two arms,
a line round the tile), "small" (17 to 32 px: a little larger eyes, a 1 px line round the tile) and "tiny"
(16 px: thicker arms, a larger mantle, the eyes on whole pixels, no line). The name is IBM Plex Sans SemiBold
(SIL Open Font License), as outlines, so no font file is needed.
"""
import base64, json, math, os, re, shutil, struct, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
TILE_TOP, TILE_BOTTOM, BORDER = "#13254a", "#08101f", "#24365e"
OCTOPUS, EYES, SUCKERS = "#c8323f", "#ffc94d", "#ef8a8f"
INK_LIGHT, PM_LIGHT, INK_DARK, PM_DARK = "#141826", "#a3202d", "#eceef4", "#ef6b74"   # the name on a light and a dark page


def num(v):
    s = ("%.2f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def curve(pts):
    """Points on a smooth curve (Catmull-Rom) through pts, 10 for each part."""
    P = lambda i: pts[max(0, min(len(pts) - 1, i))]
    out = []
    for i in range(len(pts) - 1):
        for k in range(10):
            t = k / 10
            t2 = t * t
            t3 = t2 * t
            p0, p1, p2, p3 = P(i - 1), P(i), P(i + 1), P(i + 2)
            out.append([.5 * (2 * p1[a] + (-p0[a] + p2[a]) * t + (2 * p0[a] - 5 * p1[a] + 4 * p2[a] - p3[a]) * t2
                              + (-p0[a] + 3 * p1[a] - 3 * p2[a] + p3[a]) * t3) for a in (0, 1)])
    out.append(pts[-1])
    return out


def arm_path(pts, w0, w1):
    """A tapered arm along the curve through pts: w0 wide at the body, w1 at the tip."""
    S = curve(pts)
    left, right = [], []
    for i, p in enumerate(S):
        a, b = S[max(0, i - 1)], S[min(len(S) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy) or 1
        dx, dy = dx / n, dy / n
        w = (w0 + (w1 - w0) * i / (len(S) - 1)) / 2
        left.append([p[0] - dy * w, p[1] + dx * w])
        right.append([p[0] + dy * w, p[1] - dx * w])
    return "M" + "L".join(num(x) + " " + num(y) for x, y in left + right[::-1]) + "z"


def evil_eye(cx, cy, r):
    """An eye with a slanted lid: a circle cut by a line that goes down to the nose (the right side)."""
    a, k = math.radians(24), .1
    sn, cs = math.sin(a), math.cos(a)
    m = k * r * sn
    q = math.sqrt(m * m - k * k * r * r + r * r)
    at = lambda t: (num(cx + t * cs), num(cy - k * r + t * sn))
    (ax, ay), (bx, by) = at(m - q), at(m + q)
    return f'<path d="M{ax} {ay}L{bx} {by}A{r:g} {r:g} 0 1 1 {ax} {ay}z"/>'


BODY = [15.5, 13]
ARMS = [[[10.6, 13.6], [6.6, 12.4], [3.6, 13.8], [3, 16.6]], [[11.4, 16], [7.4, 17.4], [5, 20.6], [5.6, 23.6]],
        [[12.8, 18], [10.4, 21.4], [10.6, 25], [8.4, 27.6]], [[14.8, 19.4], [15, 23.4], [13.4, 26.6], [14.4, 29.4]],
        [[16.8, 19.4], [18.6, 22.8], [18, 26.4], [20, 28.8]], [[18.8, 17.8], [22, 20], [23.4, 23.6], [26, 24.8]],
        [[20.2, 15.4], [24, 16], [26.8, 18.6], [29, 17.8]], [[20.2, 12.6], [23.6, 10.2], [27, 10.4], [28.6, 7.8]]]
EYE_PAIR = [(13.4, 10.7, 1.25), (16.4, 11.5, 1.45)]   # the far eye and the near eye
EYES_16 = '<path d="M10 8.6L13 10V12H10z"/><path d="M18 9.6L15 11V13H18z"/>'


def level_of(size):
    return "tiny" if size <= 16 else "small" if size <= 32 else "large"


def mark(level="large", square=False):
    """The inside of the mark in a 32 x 32 box. square: no round corners (for apple-touch-icon.png)."""
    tiny = level == "tiny"
    arms = [[BODY] + a for a in ARMS]
    w0, w1 = (3.6, 1.8) if tiny else (3.3, 1)
    rx = 0 if square else 7
    out = (f'<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{TILE_TOP}"/>'
           f'<stop offset="1" stop-color="{TILE_BOTTOM}"/></linearGradient><clipPath id="c"><rect width="32" height="32" rx="{rx}"/></clipPath></defs>'
           f'<rect width="32" height="32" rx="{rx}" fill="url(#g)"/><g clip-path="url(#c)"><g fill="{OCTOPUS}">'
           + "".join(f'<path d="{arm_path(a, w0, w1)}"/>' for a in arms)
           + f'<ellipse cx="10.6" cy="7.4" rx="{5.8 if tiny else 5.4}" ry="{4.6 if tiny else 4.1}" transform="rotate(-32 10.6 7.4)"/>'
           + '<path d="M9.5 10.5C12 9 15.5 8.2 17.6 9.6C19.8 11.2 19.4 14.6 17 15.8C14.6 16.8 11.6 15.6 10.6 13.4z"/></g>')
    if level == "large":   # a row of suckers on the outer half of two arms
        dots = ""
        for i in (7, 0):
            C = curve(arms[i])
            for j in range(round(len(C) * .5), len(C) - 3, 3):
                p, q = C[j], C[j + 1]
                dx, dy = q[0] - p[0], q[1] - p[1]
                m = math.hypot(dx, dy) or 1
                w = (w0 + (w1 - w0) * j / (len(C) - 1)) / 2
                dots += f'<circle cx="{num(p[0] + dy / m * w * .45)}" cy="{num(p[1] - dx / m * w * .45)}" r="{num(max(.28, w * .32))}"/>'
        out += f'<g fill="{SUCKERS}">{dots}</g>'
    grow = .15 if level == "small" else 0
    (x0, y0, r0), (x1, y1, r1) = EYE_PAIR
    face = EYES_16 if tiny else (evil_eye(x0, y0, r0 + grow) + f'<g transform="translate({2 * x1:g} 0) scale(-1 1)">{evil_eye(x1, y1, r1 + grow)}</g>')
    out += f'<g fill="{EYES}">{face}</g></g>'
    if not tiny and not square:
        out += ('<rect x=".5" y=".5" width="31" height="31" rx="6.6" fill="none" stroke="%s" stroke-width="1"/>' % BORDER if level == "small"
                else '<rect x=".3" y=".3" width="31.4" height="31.4" rx="6.6" fill="none" stroke="%s" stroke-width=".6"/>' % BORDER)
    return out


def mark_svg(size, square=False, level=None):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="{size}" height="{size}" role="img" '
            f'aria-label="MaximizePM">{mark(level or level_of(size), square)}</svg>\n')


def app_icon_svg():
    """The macOS app icon: the tile on the icon grid (an 824 px square at 100 px in 1024), with a soft shadow."""
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024" role="img" '
            'aria-label="MaximizePM"><defs><filter id="s" filterUnits="userSpaceOnUse" x="0" y="0" width="1024" height="1024">'
            '<feDropShadow dx="0" dy="14" stdDeviation="16" flood-opacity=".3"/></filter></defs>'
            f'<g filter="url(#s)"><g transform="translate(100 100) scale(25.75)">{mark("large")}</g></g></svg>\n')


# "Maximize" and "PM" in IBM Plex Sans SemiBold at 100 units, letter spacing -0.02 em, baseline at y = 0.
NAME_WIDTH, CAP_HEIGHT = 569.6, 69.8
NAME_MAXIMIZE = "M20.7 0L8.2 0L8.2-69.8L22.8-69.8L33.3-50L40.8-35.7L41.1-35.7L48.5-50L58.9-69.8L73.5-69.8L73.5 0L61 0L61-38.6L61-49.6L60.7-49.6L55.3-39L40.8-12.6L26.5-38.9L21-50.1L20.7-50.1L20.7-38.6L20.7 0M132.6-10.2L132.6 0L125.5 0Q122.6 0 120.4-1.3Q118.1-2.7 116.9-5.3Q115.6-7.9 115.6-11.4L115.6-11.4L115.6-12.5L118.8-9L115.2-9Q113.9-4 109.9-1.4Q105.8 1.2 100 1.2L100 1.2Q92 1.2 87.7-3.1Q83.4-7.3 83.4-14.2L83.4-14.2Q83.4-19.6 86.1-23.1Q88.7-26.6 93.7-28.4Q98.7-30.2 105.7-30.2L105.7-30.2L114.6-30.2L114.6-34Q114.6-38.3 112.3-40.7Q110-43.2 104.9-43.2L104.9-43.2Q100.4-43.2 97.7-41.2Q94.9-39.3 93-36.6L93-36.6L85.4-43.4Q88.3-47.9 93.1-50.7Q97.9-53.4 105.8-53.4L105.8-53.4Q116.3-53.4 121.9-48.6Q127.4-43.7 127.4-34.8L127.4-34.8L127.4-10.2L132.6-10.2M114.6-15.6L114.6-22.5L106.4-22.5Q101.4-22.5 98.9-20.8Q96.4-19.2 96.4-16.1L96.4-16.1L96.4-14.4Q96.4-11.3 98.5-9.7Q100.6-8.1 104.3-8.1L104.3-8.1Q107.2-8.1 109.5-9Q111.8-9.8 113.2-11.5Q114.6-13.2 114.6-15.6L114.6-15.6M149 0L135.5 0L153.3-26.5L135.9-52.2L150.5-52.2L161.3-34.9L161.6-34.9L172.3-52.2L186-52.2L168.3-26.3L186.1 0L171.5 0L160.5-18L160.1-18L149 0M199.8-59.8L199.8-59.8Q195.9-59.8 194.1-61.6Q192.3-63.5 192.3-66.2L192.3-66.2L192.3-68.2Q192.3-71 194.1-72.8Q195.9-74.6 199.8-74.6L199.8-74.6Q203.7-74.6 205.5-72.8Q207.3-71 207.3-68.2L207.3-68.2L207.3-66.2Q207.3-63.5 205.5-61.6Q203.7-59.8 199.8-59.8M206.2 0L193.4 0L193.4-52.2L206.2-52.2L206.2 0M231.7-34.5L231.7 0L218.9 0L218.9-52.2L231.7-52.2L231.7-43.5L232.2-43.5Q233.7-47.6 237.1-50.5Q240.4-53.4 246.2-53.4L246.2-53.4Q251.6-53.4 255.6-50.8Q259.5-48.2 261.3-42.9L261.3-42.9L261.5-42.9Q262.9-47.2 267.1-50.3Q271.3-53.4 277.5-53.4L277.5-53.4Q285.1-53.4 289.3-48.1Q293.5-42.7 293.5-33L293.5-33L293.5 0L280.7 0L280.7-31.7Q280.7-37.3 278.7-40.1Q276.6-42.9 272.3-42.9L272.3-42.9Q269.8-42.9 267.6-41.9Q265.3-41 264-39.2Q262.6-37.3 262.6-34.5L262.6-34.5L262.6 0L249.8 0L249.8-31.7Q249.8-37.3 247.7-40.1Q245.6-42.9 241.4-42.9L241.4-42.9Q239-42.9 236.8-41.9Q234.5-41 233.1-39.2Q231.7-37.3 231.7-34.5L231.7-34.5M312.1-59.8L312.1-59.8Q308.2-59.8 306.4-61.6Q304.6-63.5 304.6-66.2L304.6-66.2L304.6-68.2Q304.6-71 306.4-72.8Q308.2-74.6 312.1-74.6L312.1-74.6Q316-74.6 317.8-72.8Q319.6-71 319.6-68.2L319.6-68.2L319.6-66.2Q319.6-63.5 317.8-61.6Q316-59.8 312.1-59.8M318.5 0L305.7 0L305.7-52.2L318.5-52.2L318.5 0M370.3-10.2L370.3 0L327.5 0L327.5-10.2L354.6-42.1L328.4-42.1L328.4-52.2L369.6-52.2L369.6-42.3L342.3-10.2L370.3-10.2M400.5 1.2L400.5 1.2Q392.8 1.2 387.3-2.1Q381.8-5.5 378.9-11.6Q375.9-17.8 375.9-26.2L375.9-26.2Q375.9-34.4 378.8-40.6Q381.7-46.7 387.1-50.1Q392.5-53.4 400.1-53.4L400.1-53.4Q408.3-53.4 413.6-49.8Q418.8-46.2 421.4-40.2Q423.9-34.3 423.9-27.1L423.9-27.1L423.9-22.9L389.2-22.9L389.2-21.6Q389.2-16 392.4-12.5Q395.6-9 401.9-9L401.9-9Q406.6-9 409.7-11Q412.8-13.1 415.2-16.1L415.2-16.1L422.1-8.4Q418.9-4.1 413.4-1.5Q407.8 1.2 400.5 1.2M400.3-43.8L400.3-43.8Q396.9-43.8 394.4-42.2Q391.9-40.7 390.6-37.9Q389.2-35.2 389.2-31.6L389.2-31.6L389.2-30.8L410.6-30.8L410.6-31.7Q410.6-35.4 409.4-38.1Q408.2-40.7 405.9-42.2Q403.6-43.8 400.3-43.8"
NAME_PM = "M447.2-26.6L447.2 0L434 0L434-69.8L465.4-69.8Q471.9-69.8 476.5-67.1Q481.1-64.4 483.6-59.5Q486.1-54.7 486.1-48.2L486.1-48.2Q486.1-41.7 483.6-36.8Q481.1-32 476.5-29.3Q471.9-26.6 465.4-26.6L465.4-26.6L447.2-26.6M464.2-58.3L447.2-58.3L447.2-38L464.2-38Q466.8-38 468.6-38.9Q470.4-39.9 471.4-41.7Q472.4-43.4 472.4-45.9L472.4-45.9L472.4-50.5Q472.4-53.1 471.4-54.8Q470.4-56.5 468.6-57.4Q466.8-58.3 464.2-58.3L464.2-58.3M508.6 0L496.1 0L496.1-69.8L510.7-69.8L521.2-50L528.7-35.7L529-35.7L536.4-50L546.8-69.8L561.4-69.8L561.4 0L548.9 0L548.9-38.6L548.9-49.6L548.6-49.6L543.2-39L528.7-12.6L514.4-38.9L508.9-50.1L508.6-50.1L508.6-38.6L508.6 0"


def name_paths(theme, x, baseline, size):
    ink, pm = (INK_LIGHT, PM_LIGHT) if theme == "light" else (INK_DARK, PM_DARK)
    k = size / 100
    return (f'<g transform="translate({x:g} {baseline:g}) scale({k:g})"><path fill="{ink}" d="{NAME_MAXIMIZE}"/>'
            f'<path fill="{pm}" d="{NAME_PM}"/></g>')


def wordmark_svg(theme):
    """The name only. 100 units high; the letters stand on y = 85."""
    w = round(NAME_WIDTH + 8)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} 100" width="{w}" height="100" role="img" '
            f'aria-label="MaximizePM">{name_paths(theme, 4, 85, 100)}</svg>\n')


def logo_svg(theme):
    """The mark and the name: a 64 px tile, the name at 46 px with its capitals centred on the tile."""
    size, gap = 46, 16
    w = round(64 + gap + NAME_WIDTH * size / 100 + 2)
    baseline = 32 + CAP_HEIGHT * size / 100 / 2
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} 64" width="{w}" height="64" role="img" '
            f'aria-label="MaximizePM"><g transform="scale(2)">{mark("large")}</g>{name_paths(theme, 64 + gap, baseline, size)}</svg>\n')


MARK_SIZES = [16, 32, 48, 64, 128, 180, 192, 256, 512, 1024]
APP_SIZES = [64, 128, 256, 512, 1024]
# iconutil names: (file name, px, "app" = on the icon grid, "mark" = the tile fills the picture).
# At 16 and 32 px the tile fills the picture: on the grid the tile is 13 or 26 px wide, too small for eight arms.
ICONSET = [("icon_16x16", 16, "mark"), ("icon_16x16@2x", 32, "mark"), ("icon_32x32", 32, "mark"),
           ("icon_32x32@2x", 64, "app"), ("icon_128x128", 128, "app"), ("icon_128x128@2x", 256, "app"),
           ("icon_256x256", 256, "app"), ("icon_256x256@2x", 512, "app"), ("icon_512x512", 512, "app"),
           ("icon_512x512@2x", 1024, "app")]
CHROMES = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "/Applications/Chromium.app/Contents/MacOS/Chromium", "google-chrome", "chromium", "chromium-browser"]


def find_chrome():
    for c in [os.environ.get("CHROME")] + CHROMES:
        if c and (os.path.exists(c) or shutil.which(c)):
            return c
    return None


def render_pngs(chrome, jobs):
    """jobs: {name: (svg text, px)}. One Chrome run draws each SVG into a canvas of px x px; returns {name: PNG bytes}."""
    page = ("<!doctype html><meta charset='utf-8'><pre id='out'></pre><script>const J = " + json.dumps(
        {n: [s, px] for n, (s, px) in jobs.items()}) + """;
const out = {}; let left = Object.keys(J).length;
for (const [name, [svg, px]] of Object.entries(J)) { const img = new Image();
  img.onload = () => { const c = document.createElement('canvas'); c.width = c.height = px;
    c.getContext('2d').drawImage(img, 0, 0, px, px); out[name] = c.toDataURL('image/png');
    if (--left === 0) document.getElementById('out').textContent = 'PNGS' + JSON.stringify(out) + 'END'; };
  img.src = 'data:image/svg+xml,' + encodeURIComponent(svg); }
</script>""")
    # Chrome prints the page and then, on some computers, does not exit: read its output from a file and stop it.
    with tempfile.TemporaryDirectory() as tmp:
        path, dom = os.path.join(tmp, "render.html"), os.path.join(tmp, "dom.txt")
        with open(path, "w") as f:
            f.write(page)
        with open(dom, "w") as f:
            chrome = subprocess.Popen(
                [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                 "--force-device-scale-factor=1", "--user-data-dir=" + os.path.join(tmp, "profile"),
                 "--virtual-time-budget=8000", "--dump-dom", "file://" + path], stdout=f, stderr=subprocess.DEVNULL)
        try:
            m, end = None, time.time() + 120
            while time.time() < end:
                ended = chrome.poll() is not None
                with open(dom) as f:
                    m = re.search(r"PNGS(\{.*?\})END", f.read(), re.S)
                if m or ended:
                    break
                time.sleep(0.5)
        finally:
            chrome.kill()
            chrome.wait()
    if not m:
        sys.exit("Chrome drew no pictures (run this outside a sandbox).")
    data = json.loads(m.group(1).replace("&amp;", "&"))
    return {n: base64.b64decode(u.split(",", 1)[1]) for n, u in data.items()}


def ico(pngs):
    """An .ico file from [(px, PNG bytes)]: each picture is stored as PNG."""
    head = struct.pack("<HHH", 0, 1, len(pngs))
    offset, entries, body = 6 + 16 * len(pngs), b"", b""
    for px, data in pngs:
        entries += struct.pack("<BBBBHHII", px % 256, px % 256, 0, 0, 1, 32, len(data), offset + len(body))
        body += data
    return head + entries + body


def write(rel, data):
    path = os.path.join(HERE, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)
    print("wrote", rel)


def main():
    write("mark.svg", mark_svg(32, level="large"))
    write("mark-32.svg", mark_svg(32))
    write("mark-16.svg", mark_svg(16))
    write("app-icon.svg", app_icon_svg())
    for theme in ("light", "dark"):
        write(f"wordmark-{theme}.svg", wordmark_svg(theme))
        write(f"logo-{theme}.svg", logo_svg(theme))
    chrome = find_chrome()
    if not chrome:
        sys.exit("No Chrome found (set CHROME=<path>): the SVG files are written, the PNG, .ico and .icns files are not.")
    jobs = {f"mark-{px}": (mark_svg(px), px) for px in MARK_SIZES}
    jobs.update({f"app-icon-{px}": (app_icon_svg(), px) for px in APP_SIZES})
    jobs["apple-touch-icon"] = (mark_svg(180, square=True), 180)
    png = render_pngs(chrome, jobs)
    for name in jobs:
        if name != "apple-touch-icon":
            write(f"png/{name}.png", png[name])
    write("apple-touch-icon.png", png["apple-touch-icon"])
    for px in (16, 32, 48):
        write(f"favicon-{px}.png", png[f"mark-{px}"])
    write("favicon.ico", ico([(px, png[f"mark-{px}"]) for px in (16, 32, 48)]))
    if not shutil.which("iconutil"):
        sys.exit("No iconutil (macOS only): MaximizePM.icns is not written.")
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "MaximizePM.iconset")
        os.makedirs(iconset)
        for name, px, kind in ICONSET:
            with open(os.path.join(iconset, name + ".png"), "wb") as f:
                f.write(png[f"mark-{px}" if kind == "mark" else f"app-icon-{px}"])
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(HERE, "MaximizePM.icns")], check=True)
    print("wrote MaximizePM.icns")


if __name__ == "__main__":
    main()
