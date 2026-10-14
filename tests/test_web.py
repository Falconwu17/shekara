"""Фронтенд без сборки: проверяем, что модули web/js ссылаются на существующие файлы и экспорты,
а index.html и CSS: на существующие файлы. Ловит опечатки до запуска браузера."""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
JS = WEB / "js"

IMPORT = re.compile(r'import\s+(?:\{([^}]*)\}|\*\s+as\s+\w+)\s+from\s+"([^"]+)"')
EXPORT = re.compile(r"export\s+(?:async\s+)?(?:function|const|let|class)\s+([\w$]+)")


def modules():
    return sorted(JS.rglob("*.js"))


def test_imports_resolve_to_existing_exports():
    for module in modules():
        source = module.read_text(encoding="utf-8")
        for names, target in IMPORT.findall(source):
            path = (module.parent / target).resolve()
            assert path.exists(), f"{module.relative_to(WEB)}: нет файла {target}"
            exported = set(EXPORT.findall(path.read_text(encoding="utf-8")))
            for name in filter(None, (n.strip().split(" as ")[0] for n in names.split(","))):
                assert name in exported, f"{module.relative_to(WEB)}: {target} не экспортирует {name}"


def test_every_page_has_mount_and_is_routed():
    router = (JS / "router.js").read_text(encoding="utf-8")
    for page in (JS / "pages").glob("*.js"):
        assert "export function mount(" in page.read_text(encoding="utf-8"), page.name
        assert f'"./pages/{page.name}"' in router, f"страница {page.name} не подключена в router.js"


def test_index_links_existing_files():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    local = [ref for ref in re.findall(r'(?:href|src)="([^"#:]+)"', html)]
    assert "js/main.js" in local
    for ref in local:
        assert (WEB / ref).exists(), ref


def test_no_inner_html():
    # данные с сервера попадают в страницу только как текст (core/dom.js)
    for module in modules():
        assert "innerHTML" not in module.read_text(encoding="utf-8"), module.name
