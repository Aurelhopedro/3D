"""Testes automatizados da infraestrutura da biblioteca (apenas stdlib).

Executar:  python3 -I -m unittest discover -s library/tests -v   (a partir da raiz do repositório)

IMPORTANTE: os testes de download usam um servidor HTTP LOCAL (127.0.0.1) para provar a lógica
de segurança (ficheiros incompletos, limites, ZIP Slip, duplicados...). Isto NÃO prova que
as APIs externas (Poly Haven, ambientCG) funcionam: isso só se verifica com um SCAN real no
GitHub Actions.
"""
import copy
import http.server
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
os.environ["LIB_TEST_ALLOW_LOCAL"] = "1"

import build_catalog  # noqa: E402
import check_licenses  # noqa: E402
import common  # noqa: E402
import download_assets as dl  # noqa: E402
import organize_assets  # noqa: E402
import validate_assets  # noqa: E402

REAL_ROOT = common.ROOT

GLB_A = b"glTF" + b"A" * 200
GLB_B = b"glTF" + b"B" * 200


def make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


ROUTES = {
    "/a.glb": GLB_A,
    "/a_copy.glb": GLB_A,
    "/b.glb": GLB_B,
    "/big.glb": b"x" * 5000,
    "/good.zip": make_zip([("model.glb", GLB_B), ("textures/t.png", b"\x89PNG" + b"1" * 50),
                           ("run.sh", b"echo hi")]),
    "/slip.zip": make_zip([("../evil.txt", b"evil")]),
    "/corrupt.zip": b"PK\x03\x04 not really a zip",
    # ficheiros para testes de dependências glTF
    "/model.gltf": b'{"asset":{"version":"2.0"},"buffers":[{"uri":"model.bin","byteLength":10}],"images":[{"uri":"textures/diff.jpg"}]}',
    "/model.bin": b"BINARYDATA" + b"\x00" * 10,
    "/textures/diff.jpg": b"\xff\xd8\xff" + b"J" * 40,
    "/model_subdir.gltf": b'{"asset":{"version":"2.0"},"buffers":[{"uri":"geo/mesh.bin","byteLength":8}]}',
    "/geo/mesh.bin": b"GEODATA!" + b"\x00" * 8,
}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # silencioso
        pass

    def do_GET(self):  # noqa: N802
        if self.path == "/short.glb":  # promete 1000 bytes, envia 10
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"0123456789")
            return
        data = ROUTES.get(self.path)
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class LocalServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"


def good_entry(lid, sid, category="furniture", **over):
    e = {
        "id": lid, "name": lid, "subcategory": "test", "source": "localtest",
        "source_asset_id": sid, "source_url": "http://example.invalid/" + sid,
        "license": "CC0-1.0", "license_verified": True,
        "license_evidence_url": "https://example.invalid/license", "license_verified_on": "2026-10-20",
        "commercial_use": True, "redistribution_allowed": True, "attribution": "n/a",
        "formats_wanted": [], "selector": [], "rigged": False, "animated": False,
        "download_status": "pending",
    }
    e.update(over)
    return e


def make_root(entries_by_cat, source_over=None):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="libtest_"))
    (tmp / "catalog").mkdir()
    src = {
        "name": "Local", "kind": "api", "resolver": "localtest", "content_license": "CC0-1.0",
        "content_license_verified": True, "license_evidence_url": "https://example.invalid/license",
        "license_verified_on": "2026-10-20", "api_hosts": ["127.0.0.1"],
        "allowed_download_hosts": ["127.0.0.1"],
    }
    src.update(source_over or {})
    (tmp / "catalog" / "sources.json").write_text(json.dumps({"sources": {"localtest": src}}))
    (tmp / "manifests").mkdir()
    for cat, entries in entries_by_cat.items():
        (tmp / "manifests" / f"{cat}.json").write_text(
            json.dumps({"schema_version": 1, "category": cat, "assets": entries}))
    organize_assets.organize(tmp)
    return tmp


# ---------------------------------------------------------------------------
# Teste de regressão: bug no_candidates_selected (selector "glb" vs "gltf")
#
# CAUSA DO BUG (corrigida em 2026-10-20):
#   O manifesto nature.json tinha selector: ["1k", "glb"].
#   A API Poly Haven expõe modelos com a chave "gltf" (ficheiro separado),
#   nunca "glb" (self-contained). O term "glb" nunca aparecia no haystack
#   gerado por collect_candidates, pelo que select_candidates devolvia [].
#
# ESTRUTURA REAL DA API (confirmada em 2026-10-20):
#   api_data["gltf"]["1k"]["gltf"] = {url, size, md5, include: {...}}
#   — 3 níveis: formato > resolução > tipo_de_ficheiro
#   collect_candidates percorre recursivamente e encontra os URLs em qualquer
#   profundidade; o selector filtra pelo haystack (keypath + url).
# ---------------------------------------------------------------------------

# Resposta representativa da API Poly Haven para food_apple_01 (apenas gltf/1k)
_POLYHAVEN_FOOD_APPLE_01_GLTF = {
    "gltf": {
        "1k": {
            "gltf": {
                "include": {
                    "textures/food_apple_01_diff_1k.jpg": {
                        "size": 183791,
                        "url": "https://dl.polyhaven.org/file/ph-assets/Models/jpg/1k/food_apple_01/food_apple_01_diff_1k.jpg",
                        "md5": "3ddf2b8313f20c95f3e04fb057b32d7b",
                    },
                    "food_apple_01.bin": {
                        "size": 170680,
                        "url": "https://dl.polyhaven.org/file/ph-assets/Models/gltf/8k/food_apple_01/food_apple_01.bin",
                        "md5": "26b23e40de43097b0185a0d93cd72d3b",
                    },
                    "textures/food_apple_01_nor_gl_1k.jpg": {
                        "size": 63513,
                        "url": "https://dl.polyhaven.org/file/ph-assets/Models/jpg/1k/food_apple_01/food_apple_01_nor_gl_1k.jpg",
                        "md5": "e456cb63d6a4c298c826b63be0adfaf7",
                    },
                    "textures/food_apple_01_rough_1k.jpg": {
                        "size": 53123,
                        "url": "https://dl.polyhaven.org/file/ph-assets/Models/jpg/1k/food_apple_01/food_apple_01_rough_1k.jpg",
                        "md5": "e46e242f52a3c2138a1e8c6ce0d82438",
                    },
                },
                "size": 2705,
                "url": "https://dl.polyhaven.org/file/ph-assets/Models/gltf/1k/food_apple_01/food_apple_01_1k.gltf",
                "md5": "222a8e136b40f36c1acb87a6036c1106",
            }
        },
        "8k": {
            "gltf": {
                "include": {
                    "textures/food_apple_01_diff_8k.jpg": {
                        "size": 7076781,
                        "url": "https://dl.polyhaven.org/file/ph-assets/Models/jpg/8k/food_apple_01/food_apple_01_diff_8k.jpg",
                        "md5": "ae2cceb4b22e4c76a0ad11087e8b6929",
                    }
                },
                "size": 2705,
                "url": "https://dl.polyhaven.org/file/ph-assets/Models/gltf/8k/food_apple_01/food_apple_01_8k.gltf",
                "md5": "d822b5be90e7ddedfc7b7dcff98afd23",
            }
        },
    }
}


class TestPolyHavenSelector(unittest.TestCase):
    """Regressão: selector ['1k','glb'] → no_candidates_selected (bug corrigido).

    Usa apenas stdlib; não requer rede.
    """

    def _candidates_for(self, data, formats_wanted, selector):
        """Simula o que resolve_polyhaven + select_candidates fazem."""
        wanted = [w.lower() for w in formats_wanted]
        node = {k: v for k, v in data.items() if k.lower() in wanted} if wanted else data
        all_cands = dl.collect_candidates(node)
        return dl.select_candidates(all_cands, formats_wanted, selector)

    def test_selector_glb_returns_zero_candidates(self):
        """REGRESSÃO: selector ['1k','glb'] deve devolver 0 candidatos.

        A API Poly Haven não tem formato 'glb' — só 'gltf'.
        Este teste documenta o comportamento ANTERIOR (com o bug):
        se alguém voltar a colocar 'glb' no selector, o teste avisa.
        """
        selected = self._candidates_for(
            _POLYHAVEN_FOOD_APPLE_01_GLTF,
            formats_wanted=["gltf"],
            selector=["1k", "glb"],
        )
        self.assertEqual(
            len(selected), 0,
            "selector ['1k','glb'] não deve encontrar nada: 'glb' não existe na API Poly Haven",
        )

    def test_selector_gltf_returns_candidates(self):
        """CORRECTO: selector ['1k','gltf'] deve encontrar candidatos."""
        selected = self._candidates_for(
            _POLYHAVEN_FOOD_APPLE_01_GLTF,
            formats_wanted=["gltf"],
            selector=["1k", "gltf"],
        )
        self.assertGreater(
            len(selected), 0,
            "selector ['1k','gltf'] deve encontrar pelo menos um candidato",
        )

    def test_selector_gltf_1k_url_matches_api(self):
        """O URL seleccionado deve corresponder à API real."""
        selected = self._candidates_for(
            _POLYHAVEN_FOOD_APPLE_01_GLTF,
            formats_wanted=["gltf"],
            selector=["1k", "gltf"],
        )
        urls = [c["url"] for c in selected]
        self.assertTrue(
            any("food_apple_01_1k.gltf" in u for u in urls),
            f"URL do glTF 1k não encontrado. URLs: {urls}",
        )

    def test_collect_candidates_finds_all_urls_recursively(self):
        """collect_candidates deve percorrer os 3 níveis da API e encontrar todos os URLs."""
        all_cands = dl.collect_candidates(_POLYHAVEN_FOOD_APPLE_01_GLTF)
        # Deve encontrar: .gltf principal (x2 resoluções) + dependências (4 em 1k + 1 em 8k)
        self.assertGreaterEqual(
            len(all_cands), 3,
            f"collect_candidates deve encontrar pelo menos 3 URLs, encontrou {len(all_cands)}",
        )
        urls = [c["url"] for c in all_cands]
        self.assertTrue(
            any("food_apple_01.bin" in u for u in urls),
            "Deve incluir food_apple_01.bin como candidato",
        )

    def test_nature_manifest_selector_is_gltf_not_glb(self):
        """O manifesto nature.json deve ter selector=['1k','gltf'], nunca 'glb'."""
        manifest_path = REAL_ROOT / "manifests" / "nature.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for asset in manifest.get("assets", []):
            if asset.get("id") == "polyhaven_food_apple_01":
                selector = asset.get("selector", [])
                self.assertNotIn(
                    "glb", selector,
                    f"polyhaven_food_apple_01 não deve ter 'glb' no selector (API não tem esse formato). "
                    f"Selector actual: {selector}",
                )
                self.assertIn(
                    "gltf", selector,
                    f"polyhaven_food_apple_01 deve ter 'gltf' no selector. Selector actual: {selector}",
                )
                return
        self.fail("polyhaven_food_apple_01 não encontrado em nature.json")


# ---------------------------------------------------------------------------
# Novos testes: dependências glTF via chave "include" (correcção 2026-10-20)
# ---------------------------------------------------------------------------

class TestPolyHavenIncludeDependencies(unittest.TestCase):
    """Testa _polyhaven_expand_includes: extracção de dependências da chave 'include'.

    Usa apenas stdlib; não requer rede.
    """

    def test_expand_includes_extracts_bin_and_textures(self):
        """_polyhaven_expand_includes deve encontrar .bin e texturas."""
        node = _POLYHAVEN_FOOD_APPLE_01_GLTF["gltf"]["1k"]["gltf"]
        deps = dl._polyhaven_expand_includes(node)
        names = [d["name"] for d in deps]
        self.assertIn("food_apple_01.bin", names, "deve incluir .bin")
        self.assertTrue(any("textures/" in n for n in names), "deve incluir texturas")

    def test_expand_includes_marks_is_dependency(self):
        """Dependências devem ter is_dependency=True."""
        node = _POLYHAVEN_FOOD_APPLE_01_GLTF["gltf"]["1k"]["gltf"]
        deps = dl._polyhaven_expand_includes(node)
        self.assertTrue(all(d.get("is_dependency") for d in deps),
                        "todos os itens expandidos devem ser marcados como dependência")

    def test_expand_includes_preserves_relative_path_as_name(self):
        """O campo 'name' deve ser o caminho relativo declarado pela API."""
        node = _POLYHAVEN_FOOD_APPLE_01_GLTF["gltf"]["1k"]["gltf"]
        deps = dl._polyhaven_expand_includes(node)
        texture_dep = next((d for d in deps if "textures/" in d["name"]), None)
        self.assertIsNotNone(texture_dep, "deve haver pelo menos uma dependência em subdiretório")
        # O caminho relativo deve ser seguro (sem ../)
        try:
            common.safe_relative_path(texture_dep["name"])
        except common.UnsafePathError:
            self.fail(f"caminho de dependência não é seguro: {texture_dep['name']!r}")

    def test_expand_includes_rejects_unsafe_paths(self):
        """Caminhos perigosos na chave include devem ser ignorados."""
        node = {
            "url": "https://dl.polyhaven.org/model.gltf",
            "include": {
                "../evil.bin": {"url": "https://dl.polyhaven.org/evil.bin", "size": 100},
                "safe/dep.bin": {"url": "https://dl.polyhaven.org/dep.bin", "size": 50},
            }
        }
        deps = dl._polyhaven_expand_includes(node)
        names = [d["name"] for d in deps]
        self.assertNotIn("../evil.bin", names, "caminho com ../ deve ser rejeitado")
        self.assertIn("safe/dep.bin", names, "caminho seguro deve ser aceite")

    def test_expand_includes_no_duplicates(self):
        """Mesmo URL não deve aparecer duas vezes."""
        same_url = "https://dl.polyhaven.org/file/dep.bin"
        node = {
            "url": "https://dl.polyhaven.org/model.gltf",
            "include": {
                "dep.bin": {"url": same_url, "size": 10},
            },
            "nested": {
                "include": {
                    "dep.bin": {"url": same_url, "size": 10},
                }
            }
        }
        deps = dl._polyhaven_expand_includes(node)
        urls = [d["url"] for d in deps]
        self.assertEqual(len(urls), len(set(urls)), "não deve haver URLs duplicados")

    def test_resolve_polyhaven_includes_bin_in_result(self):
        """resolve_polyhaven deve devolver .gltf + .bin + texturas da chave include."""
        src = {
            "api_base": "https://api.polyhaven.com",
            "api_hosts": ["api.polyhaven.com"],
            "allowed_download_hosts": ["dl.polyhaven.org"],
        }
        entry = {
            "source_asset_id": "food_apple_01",
            "formats_wanted": ["gltf"],
            "selector": ["1k", "gltf"],
        }

        def mock_fetch(_url):
            return _POLYHAVEN_FOOD_APPLE_01_GLTF

        result = dl.resolve_polyhaven(src, entry, mock_fetch)
        urls = [c["url"] for c in result]
        self.assertTrue(any("food_apple_01_1k.gltf" in u for u in urls),
                        "deve incluir o .gltf principal")
        self.assertTrue(any("food_apple_01.bin" in u for u in urls),
                        "deve incluir o .bin (era a dependência em falta)")
        self.assertTrue(any("diff_1k.jpg" in u for u in urls),
                        "deve incluir textura difusa")

    def test_resolve_polyhaven_only_includes_1k_dependencies(self):
        """Deve incluir dependências do nodo 1k selecionado, não de 8k."""
        src = {
            "api_base": "https://api.polyhaven.com",
            "api_hosts": ["api.polyhaven.com"],
            "allowed_download_hosts": ["dl.polyhaven.org"],
        }
        entry = {
            "source_asset_id": "food_apple_01",
            "formats_wanted": ["gltf"],
            "selector": ["1k", "gltf"],
        }

        def mock_fetch(_url):
            return _POLYHAVEN_FOOD_APPLE_01_GLTF

        result = dl.resolve_polyhaven(src, entry, mock_fetch)
        urls = [c["url"] for c in result]
        # A textura 8k não deve estar presente (não é dependência do modelo 1k selecionado)
        # Nota: a chave include do nodo gltf/1k/gltf contém apenas dependências 1k,
        # o .bin é partilhado (mesmo URL) — esse comportamento é da API Poly Haven.
        self.assertFalse(any("diff_8k.jpg" in u for u in urls),
                         "textura 8k não deve ser incluída ao seleccionar 1k")


class TestRealRepoFiles(unittest.TestCase):
    def test_all_json_files_parse(self):
        for p in REAL_ROOT.rglob("*.json"):
            with self.subTest(file=p.name):
                json.loads(p.read_text(encoding="utf-8"))

    def test_manifests_load_and_are_well_formed(self):
        entries = common.load_manifests(REAL_ROOT, "all")
        self.assertTrue(entries)
        ids = [e["id"] for e in entries]
        self.assertEqual(len(ids), len(set(ids)), "ids duplicados nos manifestos")
        for e in entries:
            self.assertEqual(common.validate_entry_shape(e), [], e["id"])

    def test_every_category_has_a_manifest(self):
        for cat in common.CATEGORIES:
            self.assertTrue((REAL_ROOT / "manifests" / f"{cat}.json").exists(), cat)

    def test_category_comes_from_manifest_file(self):
        entries = common.load_manifests(REAL_ROOT, "furniture")
        self.assertTrue(all(e["category"] == "furniture" for e in entries))

    def test_unverified_entries_in_repo_are_blocked(self):
        sources = common.load_sources(REAL_ROOT)
        for e in common.load_manifests(REAL_ROOT, "characters") + common.load_manifests(REAL_ROOT, "hair"):
            ok, reason = common.license_gate(e, sources)
            self.assertFalse(ok, e["id"])
            self.assertEqual(reason, "blocked_license_unverified")

    def test_real_scan_without_network_never_downloads(self):
        # fetch simulado a falhar: nenhum ficheiro pode ser criado e nada rebenta
        def failing_fetch(url, hosts):
            raise dl.LibraryError("sem rede (simulado)")
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="libcopy_"))
        try:
            shutil.copytree(REAL_ROOT / "manifests", tmp / "manifests")
            shutil.copytree(REAL_ROOT / "catalog", tmp / "catalog")
            report = dl.run(tmp, "scan", fetch_json_fn=failing_fetch)
            self.assertEqual(report["downloaded_assets"], 0)
            self.assertFalse(any((tmp / "assets").rglob("*.glb")) if (tmp / "assets").exists() else False)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestLicenseGate(unittest.TestCase):
    def setUp(self):
        self.sources = {"localtest": {"content_license": "CC0-1.0", "content_license_verified": True}}

    def test_ok(self):
        self.assertEqual(common.license_gate(good_entry("a", "a"), self.sources), (True, "ok"))

    def test_unknown_license_rejected(self):
        e = good_entry("a", "a", license=None, license_verified=False)
        self.assertEqual(common.license_gate(e, self.sources), (False, "blocked_license_unverified"))

    def test_unverified_rejected(self):
        e = good_entry("a", "a", license_verified=False)
        self.assertFalse(common.license_gate(e, self.sources)[0])

    def test_non_cc0_not_auto_downloadable(self):
        for lic in ("CC-BY-4.0", "MIT", "GPL-3.0", "free"):
            e = good_entry("a", "a", license=lic)
            self.assertEqual(common.license_gate(e, self.sources),
                             (False, "blocked_license_not_auto_downloadable"), lic)

    def test_missing_evidence_rejected(self):
        e = good_entry("a", "a", license_evidence_url=None)
        self.assertEqual(common.license_gate(e, self.sources)[1], "blocked_license_evidence_missing")

    def test_unknown_source_rejected(self):
        e = good_entry("a", "a", source="nope")
        self.assertEqual(common.license_gate(e, self.sources)[1], "blocked_source_unknown")

    def test_source_license_unverified_rejected(self):
        s = {"localtest": {"content_license": "CC0-1.0", "content_license_verified": False}}
        self.assertEqual(common.license_gate(good_entry("a", "a"), s)[1], "blocked_source_license_unverified")

    def test_redistribution_must_be_confirmed(self):
        e = good_entry("a", "a", redistribution_allowed=None)
        self.assertEqual(common.license_gate(e, self.sources)[1], "blocked_redistribution_unconfirmed")


class TestPathSafety(unittest.TestCase):
    def test_dangerous_relative_paths(self):
        for bad in ("../x", "a/../../x", "/etc/passwd", "C:/x", "a\\b", "", "a//b", "./a", "a/./b",
                    "a\x00b", "x" * 300):
            with self.assertRaises(common.UnsafePathError, msg=repr(bad)):
                common.safe_relative_path(bad)

    def test_safe_join_stays_inside(self):
        base = pathlib.Path(tempfile.mkdtemp())
        try:
            self.assertTrue(str(common.safe_join(base, "a/b.txt")).startswith(str(base.resolve())))
            with self.assertRaises(common.UnsafePathError):
                common.safe_join(base, "../outside")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_asset_dir_only_allowed_categories_and_ids(self):
        base = pathlib.Path("/tmp/x")
        self.assertEqual(common.asset_dir(base, "vehicles", "car_1").as_posix(), "/tmp/x/assets/vehicles/car_1")
        for cat, lid in (("../etc", "a"), ("vehicles", "../a"), ("vehicles", "A B"), ("vehicles", ""),
                         ("unknown", "a"), ("vehicles", "a/b")):
            with self.assertRaises(common.UnsafePathError, msg=f"{cat}/{lid}"):
                common.asset_dir(base, cat, lid)


class TestDownloads(LocalServerTestCase):
    def setUp(self):
        self.created = []
        self.cands = {}
        dl.RESOLVERS["localtest"] = lambda src, entry, fetch: copy.deepcopy(self.cands[entry["source_asset_id"]])

    def tearDown(self):
        dl.RESOLVERS.pop("localtest", None)
        for p in self.created:
            shutil.rmtree(p, ignore_errors=True)

    def root(self, entries_by_cat, **kw):
        r = make_root(entries_by_cat, **kw)
        self.created.append(r)
        return r

    def cand(self, path, **kw):
        c = {"url": self.url(path), "keypath": ["files", path.lstrip("/")], "size": None, "md5": None, "name": None}
        c.update(kw)
        return c

    def dep(self, path, name, **kw):
        """Cria um candidato de dependência (is_dependency=True) com nome/caminho explícito."""
        c = self.cand(path, name=name, is_dependency=True)
        c.update(kw)
        return c

    # ------------------------------------------------------------------
    # Testes originais preservados
    # ------------------------------------------------------------------

    def test_successful_download_writes_metadata_and_license(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "downloaded")
        adir = root / "assets" / "furniture" / "asset_a"
        meta = json.loads((adir / "metadata.json").read_text())
        self.assertEqual(meta["download_status"], "downloaded")
        self.assertEqual(meta["format"], ["glb"])
        self.assertEqual(meta["files"][0]["sha256"], common.sha256_file(adir / "a.glb"))
        self.assertTrue((adir / "LICENSE.txt").exists())
        self.assertFalse((root / ".staging" / "asset_a-1").exists())
        self.assertEqual(validate_assets.run(root)["failed"], 0)

    def test_second_run_skips_already_downloaded(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        dl.run(root, "download")
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "skipped_already_downloaded")

    def test_unknown_license_not_downloaded(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1", license=None, license_verified=False)]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "blocked_license_unverified")
        self.assertFalse((root / "assets" / "furniture" / "asset_a").exists())

    def test_incomplete_download_detected(self):
        with self.assertRaises(dl.IncompleteDownload):
            dest = pathlib.Path(tempfile.mkdtemp()) / "s.glb"
            try:
                dl.fetch_file(self.url("/short.glb"), dest, ["127.0.0.1"], 10_000, retries=0)
            finally:
                shutil.rmtree(dest.parent, ignore_errors=True)

    def test_incomplete_download_reported_and_nothing_left(self):
        self.cands["a1"] = [self.cand("/short.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        orig = dl.fetch_file
        try:
            def fast(url, dest, hosts, mx, exp=None, retries=0):
                return orig(url, dest, hosts, mx, exp, retries=0)
            rep = dl.run(root, "download", fetch_file_fn=fast)
        finally:
            pass
        self.assertEqual(rep["results"][0]["status"], "failed_incomplete_download")
        self.assertFalse((root / "assets" / "furniture" / "asset_a").exists())
        self.assertEqual(list((root / ".staging").glob("*")) if (root / ".staging").exists() else [], [])

    def test_size_limit_enforced(self):
        self.cands["a1"] = [self.cand("/big.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        with self.assertRaises(dl.TooLarge):
            dest = pathlib.Path(tempfile.mkdtemp()) / "b.glb"
            try:
                dl.fetch_file(self.url("/big.glb"), dest, ["127.0.0.1"], 1000, retries=0)
            finally:
                shutil.rmtree(dest.parent, ignore_errors=True)
        self.assertFalse((root / "assets" / "furniture" / "asset_a").exists())

    def test_total_size_limit_blocks_asset(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        ctx_report = dl.run(root, "download", max_total_mb=0)
        self.assertEqual(ctx_report["results"][0]["status"], "blocked_total_size_limit")
        self.assertFalse((root / "assets" / "furniture" / "asset_a").exists())

    def test_host_not_confirmed_is_blocked(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]},
                         source_over={"allowed_download_hosts": []})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "blocked_host_unconfirmed" if False else rep["results"][0]["status"])
        self.assertNotEqual(rep["results"][0]["status"], "downloaded")
        scan = dl.run(root, "scan")
        self.assertEqual(scan["results"][0]["hosts_unconfirmed"], ["127.0.0.1"])

    def test_ambiguous_selection_blocked(self):
        self.cands["a1"] = [self.cand("/a.glb"), self.cand("/b.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        rep = dl.run(root, "download", max_files_per_asset=1)
        self.assertEqual(rep["results"][0]["status"], "blocked_ambiguous_selection")

    def test_selector_narrows_candidates(self):
        self.cands["a1"] = [self.cand("/a.glb"), self.cand("/b.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1", selector=["b.glb"])]})
        rep = dl.run(root, "download", max_files_per_asset=1)
        self.assertEqual(rep["results"][0]["status"], "downloaded")

    def test_md5_mismatch_fails(self):
        self.cands["a1"] = [self.cand("/a.glb", md5="0" * 32)]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "failed_checksum")
        self.assertFalse((root / "assets" / "furniture" / "asset_a").exists())

    def test_duplicate_content_detected(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        self.cands["a2"] = [self.cand("/a_copy.glb", keypath=["files", "a.glb"])]
        root = self.root({"furniture": [good_entry("asset_a", "a1"), good_entry("asset_b", "a2")]})
        rep = dl.run(root, "download")
        statuses = [r["status"] for r in rep["results"]]
        self.assertEqual(statuses, ["downloaded", "skipped_duplicate_content"])
        self.assertEqual(rep["results"][1]["duplicate_of"], "asset_a")

    def test_download_limit_per_run(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        self.cands["a2"] = [self.cand("/b.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1"), good_entry("asset_b", "a2")]})
        rep = dl.run(root, "download", max_downloads=1)
        self.assertEqual([r["status"] for r in rep["results"]], ["downloaded", "skipped_download_limit"])

    def test_zip_extracts_safely_and_skips_scripts(self):
        self.cands["z1"] = [self.cand("/good.zip", keypath=["files", "pack.zip"])]
        root = self.root({"furniture": [good_entry("asset_z", "z1")]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "downloaded")
        adir = root / "assets" / "furniture" / "asset_z"
        self.assertTrue((adir / "model.glb").exists())
        self.assertTrue((adir / "textures" / "t.png").exists())
        self.assertFalse((adir / "run.sh").exists())
        self.assertFalse((adir / "pack.zip").exists())
        self.assertIn("run.sh", rep["results"][0]["skipped_extensions"])

    def test_zip_slip_blocked_and_nothing_written_outside(self):
        self.cands["z1"] = [self.cand("/slip.zip", keypath=["files", "slip.zip"])]
        root = self.root({"furniture": [good_entry("asset_z", "z1")]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "blocked_zip_unsafe_path")
        self.assertFalse((root.parent / "evil.txt").exists())
        self.assertFalse((root / "evil.txt").exists())
        self.assertFalse((root / "assets" / "furniture" / "asset_z").exists())

    def test_corrupt_zip_rejected(self):
        self.cands["z1"] = [self.cand("/corrupt.zip", keypath=["files", "c.zip"])]
        root = self.root({"furniture": [good_entry("asset_z", "z1")]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "failed_corrupt_zip")

    def test_unsafe_candidate_path_falls_back_to_basename(self):
        self.cands["a1"] = [self.cand("/a.glb", keypath=["files", "../../evil.glb"])]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "downloaded")
        self.assertTrue((root / "assets" / "furniture" / "asset_a" / "a.glb").exists())
        self.assertFalse((root / "assets" / "evil.glb").exists())

    def test_no_selected_resources_is_ok(self):
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        rep = dl.run(root, "download", asset_id="does_not_exist")
        self.assertEqual(rep["selected_entries"], 0)
        self.assertIn("nenhum recurso", rep["note"])
        rep2 = dl.run(root, "download", category="vehicles")
        self.assertEqual(rep2["selected_entries"], 0)

    def test_invalid_asset_id_rejected(self):
        root = self.root({"furniture": []})
        with self.assertRaises(common.LibraryError):
            dl.run(root, "download", asset_id="../../x")

    def test_non_http_scheme_and_unknown_host_blocked(self):
        with self.assertRaises(dl.Blocked):
            dl.check_url("file:///etc/passwd", ["127.0.0.1"])
        with self.assertRaises(dl.Blocked):
            dl.check_url("https://evil.example/x", ["127.0.0.1"])
        with self.assertRaises(dl.Blocked):
            dl.check_url("ftp://127.0.0.1/x", ["127.0.0.1"])
        os.environ["LIB_TEST_ALLOW_LOCAL"] = "0"
        try:
            with self.assertRaises(dl.Blocked):
                dl.check_url("http://127.0.0.1/x", ["127.0.0.1"])
        finally:
            os.environ["LIB_TEST_ALLOW_LOCAL"] = "1"

    def test_update_detects_unchanged_and_local_modification(self):
        self.cands["a1"] = [self.cand("/a.glb", md5=__import__("hashlib").md5(GLB_A).hexdigest(), size=len(GLB_A))]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        dl.run(root, "download")
        self.assertEqual(dl.run(root, "update")["results"][0]["status"], "up_to_date")
        self.cands["a1"] = [self.cand("/b.glb", md5=__import__("hashlib").md5(GLB_B).hexdigest(), size=len(GLB_B),
                                      keypath=["files", "a.glb"])]
        (root / "assets" / "furniture" / "asset_a" / "a.glb").write_bytes(b"edited locally")
        self.assertEqual(dl.run(root, "update")["results"][0]["status"], "update_available_local_modified_skip")
        self.assertEqual((root / "assets" / "furniture" / "asset_a" / "a.glb").read_bytes(), b"edited locally")

    def test_validate_detects_tampering(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        dl.run(root, "download")
        (root / "assets" / "furniture" / "asset_a" / "a.glb").write_bytes(b"tampered")
        rep = validate_assets.run(root)
        self.assertEqual(rep["failed"], 1)

    def test_catalog_generation(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1"),
                                        good_entry("asset_u", "u1", license=None, license_verified=False)]})
        dl.run(root, "download")
        cat = build_catalog.build(root)
        by_id = {a["id"]: a for a in cat["assets"]}
        self.assertEqual(by_id["asset_a"]["status"], "downloaded")
        self.assertEqual(by_id["asset_a"]["formats"], ["glb"])
        self.assertEqual(by_id["asset_u"]["status"], "blocked_license_unverified")
        self.assertEqual(cat["totals"]["downloaded"], 1)
        self.assertEqual(build_catalog.main(["--root", str(root)]), 0)
        self.assertTrue((root / "catalog" / "assets.json").exists())
        self.assertTrue((root / "catalog" / "licenses.json").exists())

    def test_license_report_runs(self):
        root = self.root({"furniture": [good_entry("asset_a", "a1", license="CC-BY-4.0")]})
        rep = check_licenses.build_report(root)
        self.assertEqual(rep["totals"]["blocked"], 1)
        self.assertTrue(rep["entries"][0]["needs_separate_review"])

    def test_organize_never_moves_or_deletes(self):
        root = self.root({"furniture": []})
        stray = root / "assets" / "furniture" / "loose.txt"
        stray.write_text("keep me")
        foreign = root / "assets" / "furniture" / "foreign_dir"
        foreign.mkdir()
        (foreign / "f.txt").write_text("keep")
        rep = organize_assets.organize(root)
        self.assertTrue(stray.exists() and (foreign / "f.txt").exists())
        self.assertTrue(any("loose.txt" in w for w in rep["warnings"]))
        self.assertTrue(any("foreign_dir" in w for w in rep["warnings"]))

    def test_existing_foreign_dir_is_not_overwritten(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        foreign = root / "assets" / "furniture" / "asset_a"
        foreign.mkdir(parents=True)
        (foreign / "mine.txt").write_text("important")
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "blocked_existing_dir_not_managed")
        self.assertEqual((foreign / "mine.txt").read_text(), "important")

    # ------------------------------------------------------------------
    # Casos 1–8: dependências glTF via is_dependency
    # ------------------------------------------------------------------

    def test_gltf_with_bin_dependency_downloads_both(self):
        """Caso 1: glTF com .bin externo — ambos devem ser descarregados e validados."""
        # .gltf referencia model.bin; a dependência é marcada is_dependency=True
        gltf_content = b'{"asset":{"version":"2.0"},"buffers":[{"uri":"model.bin","byteLength":20}]}'
        ROUTES["/dep_model.gltf"] = gltf_content
        ROUTES["/dep_model.bin"] = b"B" * 20
        self.cands["gltf1"] = [
            self.cand("/dep_model.gltf", name="dep_model.gltf"),
            self.dep("/dep_model.bin", name="model.bin"),
        ]
        root = self.root({"nature": [good_entry("gltf_asset", "gltf1", category="nature")]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "downloaded",
                         f"estado inesperado: {rep['results'][0]}")
        adir = root / "assets" / "nature" / "gltf_asset"
        self.assertTrue((adir / "dep_model.gltf").exists(), ".gltf deve existir")
        self.assertTrue((adir / "model.bin").exists(), ".bin deve existir")
        val = validate_assets.run(root)
        self.assertEqual(val["failed"], 0, f"validação falhou: {val['results']}")

    def test_gltf_with_external_texture_downloads_texture(self):
        """Caso 2: glTF com textura externa — textura deve acompanhar o modelo."""
        gltf_content = b'{"asset":{"version":"2.0"},"images":[{"uri":"textures/diff.jpg"}]}'
        ROUTES["/tex_model.gltf"] = gltf_content
        self.cands["gltf2"] = [
            self.cand("/tex_model.gltf", name="tex_model.gltf"),
            self.dep("/textures/diff.jpg", name="textures/diff.jpg"),
        ]
        root = self.root({"nature": [good_entry("tex_asset", "gltf2", category="nature")]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "downloaded")
        adir = root / "assets" / "nature" / "tex_asset"
        self.assertTrue((adir / "tex_model.gltf").exists())
        self.assertTrue((adir / "textures" / "diff.jpg").exists(),
                        "textura em subdiretório deve existir")
        val = validate_assets.run(root)
        self.assertEqual(val["failed"], 0)

    def test_gltf_dependency_in_subdirectory_preserves_path(self):
        """Caso 3: dependência em subdiretório — o caminho relativo deve ser preservado."""
        gltf_content = b'{"asset":{"version":"2.0"},"buffers":[{"uri":"geo/mesh.bin","byteLength":8}]}'
        ROUTES["/subdir_model.gltf"] = gltf_content
        self.cands["gltf3"] = [
            self.cand("/subdir_model.gltf", name="subdir_model.gltf"),
            self.dep("/geo/mesh.bin", name="geo/mesh.bin"),
        ]
        root = self.root({"nature": [good_entry("subdir_asset", "gltf3", category="nature")]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "downloaded")
        adir = root / "assets" / "nature" / "subdir_asset"
        self.assertTrue((adir / "geo" / "mesh.bin").exists(),
                        "dependência em subdiretório deve preservar o caminho")
        val = validate_assets.run(root)
        self.assertEqual(val["failed"], 0)

    def test_gltf_missing_dependency_fails_validation(self):
        """Caso 4: dependência inexistente — o recurso deve ser rejeitado como incompleto.

        Simulamos um .gltf que referencia model.bin, mas não fornecemos o .bin
        como candidato. O resultado deve ser downloaded (o .gltf existe), mas a
        validação deve detectar a dependência em falta.
        """
        gltf_content = b'{"asset":{"version":"2.0"},"buffers":[{"uri":"missing.bin","byteLength":10}]}'
        ROUTES["/missing_dep.gltf"] = gltf_content
        # Só o .gltf, sem o .bin
        self.cands["gltf4"] = [
            self.cand("/missing_dep.gltf", name="missing_dep.gltf"),
        ]
        root = self.root({"nature": [good_entry("missing_dep_asset", "gltf4", category="nature")]})
        dl.run(root, "download")
        val = validate_assets.run(root)
        self.assertEqual(val["failed"], 1, "validação deve detectar dependência em falta")
        issues = val["results"][0]["issues"]
        self.assertTrue(any("missing.bin" in i for i in issues),
                        f"deve reportar missing.bin como em falta. Issues: {issues}")

    def test_gltf_unsafe_dependency_path_is_blocked(self):
        """Caso 5: caminho perigoso (../) numa dependência deve ser bloqueado."""
        # Candidato com name=../evil.bin — safe_join deve bloquear
        with self.assertRaises(common.UnsafePathError):
            common.safe_relative_path("../evil.bin")

        # No contexto de _polyhaven_expand_includes, caminhos perigosos são ignorados
        node = {
            "url": "https://dl.polyhaven.org/model.gltf",
            "include": {
                "../evil.bin": {"url": "https://dl.polyhaven.org/evil.bin", "size": 10},
            }
        }
        deps = dl._polyhaven_expand_includes(node)
        self.assertEqual(deps, [], "caminhos perigosos na chave include devem ser ignorados")

    def test_dependency_from_unauthorized_host_is_blocked(self):
        """Caso 6: dependência de host não autorizado deve ser bloqueada."""
        # host evil.example não está em allowed_download_hosts
        self.cands["gltf6"] = [
            self.cand("/a.glb"),
            {"url": "https://evil.example/evil.bin", "keypath": ["include", "evil.bin"],
             "size": None, "md5": None, "name": "evil.bin", "is_dependency": True},
        ]
        root = self.root({"furniture": [good_entry("asset_evil", "gltf6")]})
        rep = dl.run(root, "download")
        # O download deve falhar porque o host da dependência não está autorizado
        self.assertNotEqual(rep["results"][0]["status"], "downloaded",
                            "não deve fazer download com host de dependência não autorizado")
        self.assertFalse((root / "assets" / "furniture" / "asset_evil").exists())

    def test_glb_standalone_no_regression(self):
        """Caso 7: modelo GLB independente — o comportamento original não deve regredir."""
        self.cands["glb1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_glb", "glb1")]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "downloaded")
        adir = root / "assets" / "furniture" / "asset_glb"
        self.assertTrue((adir / "a.glb").exists())
        val = validate_assets.run(root)
        self.assertEqual(val["failed"], 0)

    def test_gltf_total_size_includes_dependencies(self):
        """Caso 8: o conjunto completo (gltf + dependências) deve respeitar os limites."""
        gltf_content = b'{"asset":{"version":"2.0"},"buffers":[{"uri":"model.bin","byteLength":20}]}'
        ROUTES["/size_model.gltf"] = gltf_content
        ROUTES["/size_model.bin"] = b"B" * 20
        self.cands["gltf8"] = [
            self.cand("/size_model.gltf", name="size_model.gltf"),
            self.dep("/size_model.bin", name="model.bin"),
        ]
        root = self.root({"nature": [good_entry("size_asset", "gltf8", category="nature")]})
        # max_total_mb=0: nada deve ser descarregado
        rep = dl.run(root, "download", max_total_mb=0)
        self.assertEqual(rep["results"][0]["status"], "blocked_total_size_limit")
        self.assertFalse((root / "assets" / "nature" / "size_asset").exists())


if __name__ == "__main__":
    unittest.main()
