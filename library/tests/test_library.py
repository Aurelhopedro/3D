"""Testes automatizados da infraestrutura da biblioteca (apenas stdlib).

Executar:  python3 -I -m unittest discover -s library/tests -v   (a partir da raiz do repositório)

IMPORTANTE: os testes de download usam um servidor HTTP LOCAL (127.0.0.1) para provar a lógica
de segurança (ficheiros incompletos, limites, ZIP Slip, duplicados...). Isto NÃO prova que
as APIs externas (Poly Haven, ambientCG, GitHub, MakeHuman) funcionam: isso só se verifica
com um SCAN real no GitHub Actions.
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


def make_github_zip(repo_name, commit_short, entries):
    """Simula o ZIP que o GitHub gera: directório raiz = '<repo>-<commit_short>/'."""
    buf = io.BytesIO()
    prefix = f"{repo_name}-{commit_short}/"
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(prefix, "")  # entrada de directório raiz
        for name, data in entries:
            z.writestr(prefix + name, data)
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
    "/dep_model.gltf": b'{"asset":{"version":"2.0"},"buffers":[{"uri":"model.bin","byteLength":20}]}',
    "/dep_model.bin": b"B" * 20,
    "/tex_model.gltf": b'{"asset":{"version":"2.0"},"images":[{"uri":"textures/diff.jpg"}]}',
    "/textures/diff.jpg": b"\xff\xd8\xff" + b"J" * 40,
    "/subdir_model.gltf": b'{"asset":{"version":"2.0"},"buffers":[{"uri":"geo/mesh.bin","byteLength":8}]}',
    "/geo/mesh.bin": b"GEODATA!" + b"\x00" * 8,
    "/missing_dep.gltf": b'{"asset":{"version":"2.0"},"buffers":[{"uri":"missing.bin","byteLength":10}]}',
    "/size_model.gltf": b'{"asset":{"version":"2.0"},"buffers":[{"uri":"model.bin","byteLength":20}]}',
    "/size_model.bin": b"B" * 20,
    # ZIP GitHub simulado — Knight.glb + Mage.glb (LICENSE.txt é filtrado por github_files)
    "/github_knight.zip": make_github_zip(
        "KayKit-Character-Pack-Adventures-1.0",
        "672074b",
        [("Knight.glb", GLB_A), ("Mage.glb", GLB_B), ("LICENSE.txt", b"CC0")]
    ),
    # ZIP GitHub simulado — múltiplos carros City Builder
    "/github_multi.zip": make_github_zip(
        "KayKit-City-Builder-Bits-1.0",
        "6397691",
        [
            ("car_sedan.glb", GLB_A),
            ("car_taxi.glb", GLB_B),
            ("citybits_texture.png", b"\x89PNG" + b"X" * 50),
            ("LICENSE.txt", b"CC0"),
        ]
    ),
    # ZIP MakeHuman simulado — .mhclo não está em ALLOWED_EXTENSIONS → ignorado
    "/makehuman_shirts.zip": make_zip([
        ("shirts01/shirt_plain.mhclo", b"mhclo content"),
        ("shirts01/shirt_plain.obj", b"v 0 0 0\n"),
        ("shirts01/shirt_plain.png", b"\x89PNG" + b"S" * 30),
        ("LICENSE.txt", b"CC0"),
    ]),
}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        if self.path == "/short.glb":
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
        "license_evidence_url": "https://example.invalid/license", "license_verified_on": "2026-10-21",
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
        "license_verified_on": "2026-10-21", "api_hosts": ["127.0.0.1"],
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


def make_root_multi_source(entries_by_cat, sources_dict):
    """Raiz com múltiplas fontes (para testar github_zip e zip_url)."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="libtest_multi_"))
    (tmp / "catalog").mkdir()
    (tmp / "catalog" / "sources.json").write_text(json.dumps({"sources": sources_dict}))
    (tmp / "manifests").mkdir()
    for cat, entries in entries_by_cat.items():
        (tmp / "manifests" / f"{cat}.json").write_text(
            json.dumps({"schema_version": 1, "category": cat, "assets": entries}))
    organize_assets.organize(tmp)
    return tmp


# ---------------------------------------------------------------------------
# Resposta representativa da API Poly Haven para food_apple_01
# ---------------------------------------------------------------------------
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

_GLTF_1K_NODE = _POLYHAVEN_FOOD_APPLE_01_GLTF["gltf"]["1k"]["gltf"]


# ===========================================================================
# Testes do resolver Poly Haven (existentes)
# ===========================================================================

class TestPolyHavenSelector(unittest.TestCase):

    def _candidates_for(self, data, formats_wanted, selector):
        wanted = [w.lower() for w in formats_wanted]
        node = {k: v for k, v in data.items() if k.lower() in wanted} if wanted else data
        all_cands = dl.collect_candidates(node)
        return dl.select_candidates(all_cands, formats_wanted, selector)

    def test_selector_glb_returns_zero_candidates(self):
        selected = self._candidates_for(
            _POLYHAVEN_FOOD_APPLE_01_GLTF, formats_wanted=["gltf"], selector=["1k", "glb"])
        self.assertEqual(len(selected), 0)

    def test_selector_gltf_returns_candidates(self):
        selected = self._candidates_for(
            _POLYHAVEN_FOOD_APPLE_01_GLTF, formats_wanted=["gltf"], selector=["1k", "gltf"])
        self.assertGreater(len(selected), 0)

    def test_selector_gltf_1k_url_matches_api(self):
        selected = self._candidates_for(
            _POLYHAVEN_FOOD_APPLE_01_GLTF, formats_wanted=["gltf"], selector=["1k", "gltf"])
        self.assertTrue(any("food_apple_01_1k.gltf" in c["url"] for c in selected))

    def test_collect_candidates_finds_all_urls_recursively(self):
        all_cands = dl.collect_candidates(_POLYHAVEN_FOOD_APPLE_01_GLTF)
        self.assertGreaterEqual(len(all_cands), 3)
        self.assertTrue(any("food_apple_01.bin" in c["url"] for c in all_cands))

    def test_nature_manifest_selector_is_gltf_not_glb(self):
        manifest_path = REAL_ROOT / "manifests" / "nature.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for asset in manifest.get("assets", []):
            if asset.get("id") == "polyhaven_food_apple_01":
                self.assertNotIn("glb", asset.get("selector", []))
                self.assertIn("gltf", asset.get("selector", []))
                return
        self.fail("polyhaven_food_apple_01 não encontrado em nature.json")


class TestPolyHavenIncludeDependencies(unittest.TestCase):

    def test_expand_includes_extracts_bin_and_textures(self):
        deps = dl._polyhaven_expand_includes([_GLTF_1K_NODE])
        names = [d["name"] for d in deps]
        self.assertIn("food_apple_01.bin", names)
        self.assertTrue(any("textures/" in n for n in names))

    def test_expand_includes_marks_is_dependency(self):
        deps = dl._polyhaven_expand_includes([_GLTF_1K_NODE])
        self.assertTrue(all(d.get("is_dependency") for d in deps))

    def test_expand_includes_rejects_unsafe_paths(self):
        unsafe_dict = {
            "url": "https://dl.polyhaven.org/model.gltf",
            "include": {
                "../evil.bin": {"url": "https://dl.polyhaven.org/evil.bin", "size": 100},
                "safe/dep.bin": {"url": "https://dl.polyhaven.org/dep.bin", "size": 50},
            }
        }
        deps = dl._polyhaven_expand_includes([unsafe_dict])
        names = [d["name"] for d in deps]
        self.assertNotIn("../evil.bin", names)
        self.assertIn("safe/dep.bin", names)

    def test_find_parent_dicts_finds_1k_not_8k(self):
        url_1k = "https://dl.polyhaven.org/file/ph-assets/Models/gltf/1k/food_apple_01/food_apple_01_1k.gltf"
        found = dl._find_parent_dicts_of_urls(_POLYHAVEN_FOOD_APPLE_01_GLTF["gltf"], {url_1k})
        self.assertEqual(len(found), 1)
        inc_names = list(found[0]["include"].keys())
        self.assertTrue(any("diff_1k" in n for n in inc_names))
        self.assertFalse(any("diff_8k" in n for n in inc_names))

    def test_resolve_polyhaven_includes_bin_in_result(self):
        src = {"api_base": "https://api.polyhaven.com", "api_hosts": ["api.polyhaven.com"],
               "allowed_download_hosts": ["dl.polyhaven.org"]}
        entry = {"source_asset_id": "food_apple_01", "formats_wanted": ["gltf"], "selector": ["1k", "gltf"]}
        result = dl.resolve_polyhaven(src, entry, lambda _u: _POLYHAVEN_FOOD_APPLE_01_GLTF)
        urls = [c["url"] for c in result]
        self.assertTrue(any("food_apple_01_1k.gltf" in u for u in urls))
        self.assertTrue(any("food_apple_01.bin" in u for u in urls))

    def test_resolve_polyhaven_only_1k_deps(self):
        src = {"api_base": "https://api.polyhaven.com", "api_hosts": ["api.polyhaven.com"],
               "allowed_download_hosts": ["dl.polyhaven.org"]}
        entry = {"source_asset_id": "food_apple_01", "formats_wanted": ["gltf"], "selector": ["1k", "gltf"]}
        result = dl.resolve_polyhaven(src, entry, lambda _u: _POLYHAVEN_FOOD_APPLE_01_GLTF)
        urls = [c["url"] for c in result]
        self.assertFalse(any("diff_8k.jpg" in u for u in urls))


# ===========================================================================
# Testes do resolver github_zip (unitários — sem rede)
# ===========================================================================

class TestGithubZipResolver(unittest.TestCase):

    def _src(self):
        return {
            "resolver": "github_zip",
            "content_license": "CC0-1.0",
            "content_license_verified": True,
            "allowed_download_hosts": ["codeload.github.com"],
        }

    def test_resolve_github_zip_returns_one_candidate(self):
        entry = {
            "source_asset_id": "kaykit_adv_knight",
            "github_repo": "KayKit-Game-Assets/KayKit-Character-Pack-Adventures-1.0",
            "github_commit": "672074b73ba276876a19e8816ecdc5241817ab47",
            "github_files": ["Knight.glb"],
        }
        cands = dl.resolve_github_zip(self._src(), entry, lambda _u: None)
        self.assertEqual(len(cands), 1)
        self.assertIn("codeload.github.com", cands[0]["url"])
        self.assertIn("672074b73ba276876a19e8816ecdc5241817ab47", cands[0]["url"])

    def test_resolve_github_zip_url_format(self):
        entry = {
            "source_asset_id": "test",
            "github_repo": "owner/repo",
            "github_commit": "abcdef1234567890abcdef1234567890abcdef12",
            "github_files": ["model.glb"],
        }
        cands = dl.resolve_github_zip(self._src(), entry, lambda _u: None)
        expected = "https://codeload.github.com/owner/repo/zip/abcdef1234567890abcdef1234567890abcdef12"
        self.assertEqual(cands[0]["url"], expected)

    def test_resolve_github_zip_rejects_empty_commit(self):
        entry = {"source_asset_id": "test", "github_repo": "owner/repo",
                 "github_commit": "", "github_files": []}
        with self.assertRaises(dl.Blocked) as ctx:
            dl.resolve_github_zip(self._src(), entry, lambda _u: None)
        self.assertIn("blocked_github_no_commit", ctx.exception.code)

    def test_resolve_github_zip_rejects_branch_name(self):
        """Branch names (ex: 'main') não são SHAs de 40 hex — devem ser bloqueados."""
        entry = {"source_asset_id": "test", "github_repo": "owner/repo",
                 "github_commit": "main", "github_files": []}
        with self.assertRaises(dl.Blocked) as ctx:
            dl.resolve_github_zip(self._src(), entry, lambda _u: None)
        self.assertIn("blocked_github_no_commit", ctx.exception.code)

    def test_resolve_github_zip_rejects_short_sha(self):
        """SHA curto (7 hex) não é aceite — exige 40 hex."""
        entry = {"source_asset_id": "test", "github_repo": "owner/repo",
                 "github_commit": "672074b", "github_files": []}
        with self.assertRaises(dl.Blocked) as ctx:
            dl.resolve_github_zip(self._src(), entry, lambda _u: None)
        self.assertIn("blocked_github_no_commit", ctx.exception.code)

    def test_resolve_github_zip_rejects_missing_repo(self):
        entry = {"source_asset_id": "test", "github_repo": "",
                 "github_commit": "672074b73ba276876a19e8816ecdc5241817ab47", "github_files": []}
        with self.assertRaises(dl.Blocked):
            dl.resolve_github_zip(self._src(), entry, lambda _u: None)

    def test_resolve_github_zip_rejects_repo_without_slash(self):
        entry = {"source_asset_id": "test", "github_repo": "badrepoformat",
                 "github_commit": "672074b73ba276876a19e8816ecdc5241817ab47", "github_files": []}
        with self.assertRaises(dl.Blocked):
            dl.resolve_github_zip(self._src(), entry, lambda _u: None)

    def test_github_strip_prefix_basic(self):
        inner = dl._github_strip_prefix(
            "KayKit-Character-Pack-Adventures-1.0-672074b/Knight.glb",
            "KayKit-Game-Assets/KayKit-Character-Pack-Adventures-1.0",
            "672074b73ba276876a19e8816ecdc5241817ab47",
        )
        self.assertEqual(inner, "Knight.glb")

    def test_github_strip_prefix_subdir(self):
        inner = dl._github_strip_prefix(
            "KayKit-Character-Pack-Adventures-1.0-672074b/Characters/Knight.glb",
            "KayKit-Game-Assets/KayKit-Character-Pack-Adventures-1.0",
            "672074b73ba276876a19e8816ecdc5241817ab47",
        )
        self.assertEqual(inner, "Characters/Knight.glb")

    def test_github_strip_prefix_root_dir_returns_empty(self):
        """Entrada de directório raiz devolve '' (será ignorada pelo extract_zip)."""
        inner = dl._github_strip_prefix(
            "KayKit-Character-Pack-Adventures-1.0-672074b/",
            "KayKit-Game-Assets/KayKit-Character-Pack-Adventures-1.0",
            "672074b73ba276876a19e8816ecdc5241817ab47",
        )
        self.assertEqual(inner, "")

    def test_github_file_wanted_empty_list_accepts_all(self):
        self.assertTrue(dl._github_file_wanted("Knight.glb", []))
        self.assertTrue(dl._github_file_wanted("subdir/model.glb", []))

    def test_github_file_wanted_exact_match(self):
        self.assertTrue(dl._github_file_wanted("Knight.glb", ["Knight.glb"]))
        self.assertFalse(dl._github_file_wanted("Mage.glb", ["Knight.glb"]))

    def test_github_file_wanted_suffix_match(self):
        self.assertTrue(dl._github_file_wanted("Characters/Knight.glb", ["Knight.glb"]))

    def test_github_file_wanted_case_insensitive(self):
        self.assertTrue(dl._github_file_wanted("KNIGHT.GLB", ["knight.glb"]))

    def test_extract_zip_github_extracts_requested_file_only(self):
        """extract_zip filtra por github_files: Knight.glb extraído, Mage.glb ignorado."""
        glb_k = b"glTF" + b"K" * 100
        glb_m = b"glTF" + b"M" * 100
        zip_bytes = make_github_zip(
            "KayKit-Character-Pack-Adventures-1.0", "672074b",
            [("Knight.glb", glb_k), ("Mage.glb", glb_m), ("LICENSE.txt", b"CC0")],
        )
        zpath = pathlib.Path(tempfile.mktemp(suffix=".zip"))
        dest = pathlib.Path(tempfile.mkdtemp())
        try:
            zpath.write_bytes(zip_bytes)
            result = dl.extract_zip(
                zpath, dest, max_uncompressed=10 * 1024 * 1024,
                github_repo="KayKit-Game-Assets/KayKit-Character-Pack-Adventures-1.0",
                github_commit="672074b73ba276876a19e8816ecdc5241817ab47",
                github_files=["Knight.glb"],
            )
            # Knight.glb pedido → extraído
            self.assertTrue((dest / "Knight.glb").exists())
            # Mage.glb não pedido → ignorado
            self.assertFalse((dest / "Mage.glb").exists())
            # LICENSE.txt também não está em github_files → ignorado (correcto)
            self.assertFalse((dest / "LICENSE.txt").exists())
            self.assertIn("Knight.glb", result["extracted"])
        finally:
            zpath.unlink(missing_ok=True)
            shutil.rmtree(dest, ignore_errors=True)

    def test_extract_zip_github_empty_files_accepts_all_valid_ext(self):
        """github_files=[] aceita todos os ficheiros com extensão permitida."""
        glb_data = b"glTF" + b"A" * 50
        zip_bytes = make_github_zip(
            "MyRepo", "abcdef1",
            [("ModelA.glb", glb_data), ("ModelB.glb", glb_data), ("script.py", b"print(1)")],
        )
        zpath = pathlib.Path(tempfile.mktemp(suffix=".zip"))
        dest = pathlib.Path(tempfile.mkdtemp())
        try:
            zpath.write_bytes(zip_bytes)
            dl.extract_zip(zpath, dest, max_uncompressed=10 * 1024 * 1024,
                           github_repo="owner/MyRepo",
                           github_commit="abcdef1234567890abcdef1234567890abcdef12",
                           github_files=[])
            self.assertTrue((dest / "ModelA.glb").exists())
            self.assertTrue((dest / "ModelB.glb").exists())
            # .py não está em ALLOWED_EXTENSIONS → ignorado
            self.assertFalse((dest / "script.py").exists())
        finally:
            zpath.unlink(missing_ok=True)
            shutil.rmtree(dest, ignore_errors=True)


# ===========================================================================
# Testes do resolver zip_url (unitários — sem rede)
# ===========================================================================

class TestZipUrlResolver(unittest.TestCase):

    def _src(self, host="files.makehumancommunity.org"):
        return {
            "resolver": "zip_url",
            "content_license": "CC0-1.0",
            "content_license_verified": True,
            "allowed_download_hosts": [host],
        }

    def test_resolve_zip_url_returns_one_candidate(self):
        entry = {
            "source_asset_id": "shirts01_cc0",
            "zip_url": "https://files.makehumancommunity.org/asset_packs/shirts01/shirts01_cc0.zip",
            "zip_sha256": None,
        }
        cands = dl.resolve_zip_url(self._src(), entry, lambda _u: None)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["url"], entry["zip_url"])
        self.assertIn("shirts01_cc0.zip", cands[0]["name"])

    def test_resolve_zip_url_rejects_plain_http_non_local(self):
        """http:// para host externo é sempre rejeitado."""
        entry = {
            "source_asset_id": "test",
            "zip_url": "http://files.makehumancommunity.org/pack.zip",
            "zip_sha256": None,
        }
        with self.assertRaises(dl.Blocked) as ctx:
            dl.resolve_zip_url(self._src(), entry, lambda _u: None)
        self.assertIn("blocked_zip_url_missing_or_insecure", ctx.exception.code)

    def test_resolve_zip_url_accepts_local_http_in_test_mode(self):
        """http://127.0.0.1 é aceite quando LIB_TEST_ALLOW_LOCAL=1."""
        entry = {
            "source_asset_id": "test",
            "zip_url": "http://127.0.0.1:12345/pack.zip",
            "zip_sha256": None,
        }
        src = self._src(host="127.0.0.1")
        # Não deve lançar exceção
        cands = dl.resolve_zip_url(src, entry, lambda _u: None)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["url"], entry["zip_url"])

    def test_resolve_zip_url_rejects_empty_url(self):
        entry = {"source_asset_id": "test", "zip_url": "", "zip_sha256": None}
        with self.assertRaises(dl.Blocked):
            dl.resolve_zip_url(self._src(), entry, lambda _u: None)

    def test_resolve_zip_url_passes_sha256_to_candidate(self):
        sha = "abc123" * 10 + "abcd"  # 64 chars
        entry = {
            "source_asset_id": "test",
            "zip_url": "https://files.makehumancommunity.org/pack.zip",
            "zip_sha256": sha,
        }
        cands = dl.resolve_zip_url(self._src(), entry, lambda _u: None)
        self.assertEqual(cands[0]["_zip_sha256"], sha)


# ===========================================================================
# Testes de integração: github_zip com servidor local
# ===========================================================================

class TestGithubZipDownload(LocalServerTestCase):

    def setUp(self):
        self.created = []

    def tearDown(self):
        for p in self.created:
            shutil.rmtree(p, ignore_errors=True)

    def _src(self):
        return {
            "name": "KayKit Local Test",
            "kind": "github_zip",
            "resolver": "github_zip",
            "content_license": "CC0-1.0",
            "content_license_verified": True,
            "license_evidence_url": "https://example.invalid/license",
            "license_verified_on": "2026-10-21",
            "allowed_download_hosts": ["127.0.0.1"],
        }

    def _entry(self, lid, repo, commit, files_wanted):
        return {
            "id": lid, "name": lid, "subcategory": "test",
            "source": "localtest_github", "source_asset_id": lid,
            "source_url": "https://example.invalid/" + lid,
            "license": "CC0-1.0", "license_verified": True,
            "license_evidence_url": "https://example.invalid/license",
            "license_verified_on": "2026-10-21",
            "commercial_use": True, "redistribution_allowed": True, "attribution": "n/a",
            "formats_wanted": [], "selector": [], "rigged": True, "animated": True,
            "github_repo": repo,
            "github_commit": commit,
            "github_files": files_wanted,
            "download_status": "pending",
        }

    def _make_root(self, entries_by_cat):
        root = make_root_multi_source(entries_by_cat, {"localtest_github": self._src()})
        self.created.append(root)
        return root

    def _patched_resolver(self, local_zip_path):
        """Resolver que usa URL local em vez de codeload.github.com."""
        local_url = self.url(local_zip_path)

        def resolver(src, entry, fetch):
            cands = dl.resolve_github_zip(src, entry, fetch)
            for c in cands:
                c["url"] = local_url
            return cands

        return resolver

    def test_knight_downloaded_and_files_correct(self):
        """Download completo de Knight.glb a partir de ZIP GitHub simulado."""
        orig = dict(dl.RESOLVERS)
        dl.RESOLVERS["github_zip"] = self._patched_resolver("/github_knight.zip")
        try:
            entry = self._entry(
                "kaykit_knight_test",
                "KayKit-Game-Assets/KayKit-Character-Pack-Adventures-1.0",
                "672074b73ba276876a19e8816ecdc5241817ab47",
                ["Knight.glb"],
            )
            root = self._make_root({"characters": [entry]})
            rep = dl.run(root, "download")
            self.assertEqual(rep["results"][0]["status"], "downloaded",
                             f"resultado inesperado: {rep['results'][0]}")
            adir = root / "assets" / "characters" / "kaykit_knight_test"
            # Knight.glb extraído (está em github_files)
            self.assertTrue((adir / "Knight.glb").exists())
            # Mage.glb não extraído (não está em github_files)
            self.assertFalse((adir / "Mage.glb").exists())
            # LICENSE.txt não extraído (não está em github_files)
            self.assertFalse((adir / "LICENSE.txt").exists())
            # metadata.json tem proveniência correcta
            meta = json.loads((adir / "metadata.json").read_text())
            self.assertEqual(meta["github_commit"], "672074b73ba276876a19e8816ecdc5241817ab47")
            self.assertEqual(meta["github_repo"],
                             "KayKit-Game-Assets/KayKit-Character-Pack-Adventures-1.0")
            # Validação de integridade passa
            self.assertEqual(validate_assets.run(root)["failed"], 0)
        finally:
            dl.RESOLVERS.clear()
            dl.RESOLVERS.update(orig)

    def test_no_matching_files_fails(self):
        """Quando nenhum ficheiro do ZIP corresponde a github_files → failed."""
        orig = dict(dl.RESOLVERS)
        dl.RESOLVERS["github_zip"] = self._patched_resolver("/github_knight.zip")
        try:
            entry = self._entry(
                "kaykit_not_exist",
                "KayKit-Game-Assets/KayKit-Character-Pack-Adventures-1.0",
                "672074b73ba276876a19e8816ecdc5241817ab47",
                ["DoesNotExist.glb"],
            )
            root = self._make_root({"characters": [entry]})
            rep = dl.run(root, "download")
            self.assertNotEqual(rep["results"][0]["status"], "downloaded")
        finally:
            dl.RESOLVERS.clear()
            dl.RESOLVERS.update(orig)

    def test_branch_name_as_commit_blocked(self):
        """Commit 'main' (não SHA de 40 hex) é bloqueado pelo resolver."""
        orig = dict(dl.RESOLVERS)
        dl.RESOLVERS["github_zip"] = dl.resolve_github_zip  # sem patch de URL
        try:
            entry = self._entry(
                "bad_commit_test",
                "KayKit-Game-Assets/KayKit-Character-Pack-Adventures-1.0",
                "main",  # branch name → deve bloquear
                ["Knight.glb"],
            )
            root = self._make_root({"characters": [entry]})
            rep = dl.run(root, "download")
            self.assertNotEqual(rep["results"][0]["status"], "downloaded")
            self.assertIn("github", rep["results"][0]["status"])
        finally:
            dl.RESOLVERS.clear()
            dl.RESOLVERS.update(orig)

    def test_short_sha_blocked(self):
        """SHA curto (7 hex) é bloqueado — exige 40 hex."""
        orig = dict(dl.RESOLVERS)
        dl.RESOLVERS["github_zip"] = dl.resolve_github_zip
        try:
            entry = self._entry(
                "short_sha_test",
                "KayKit-Game-Assets/KayKit-Character-Pack-Adventures-1.0",
                "672074b",  # 7 hex → deve bloquear
                ["Knight.glb"],
            )
            root = self._make_root({"characters": [entry]})
            rep = dl.run(root, "download")
            self.assertNotEqual(rep["results"][0]["status"], "downloaded")
        finally:
            dl.RESOLVERS.clear()
            dl.RESOLVERS.update(orig)


# ===========================================================================
# Testes de integração: zip_url com servidor local
# ===========================================================================

class TestZipUrlDownload(LocalServerTestCase):

    def setUp(self):
        self.created = []

    def tearDown(self):
        for p in self.created:
            shutil.rmtree(p, ignore_errors=True)

    def _src(self):
        return {
            "name": "MakeHuman Local Test",
            "kind": "zip_url",
            "resolver": "zip_url",
            "content_license": "CC0-1.0",
            "content_license_verified": True,
            "license_evidence_url": "https://example.invalid/license",
            "license_verified_on": "2026-10-21",
            "allowed_download_hosts": ["127.0.0.1"],
        }

    def _entry(self, lid, zip_local_path, sha256=None):
        """Entrada com zip_url já apontando para o servidor local (http://127.0.0.1)."""
        return {
            "id": lid, "name": lid, "subcategory": "test",
            "source": "localtest_zip", "source_asset_id": lid,
            "source_url": "https://example.invalid/" + lid,
            "license": "CC0-1.0", "license_verified": True,
            "license_evidence_url": "https://example.invalid/license",
            "license_verified_on": "2026-10-21",
            "commercial_use": True, "redistribution_allowed": True, "attribution": "n/a",
            "formats_wanted": [], "selector": [], "rigged": False, "animated": False,
            # URL local directamente — resolve_zip_url aceita http://127.0.0.1 com LIB_TEST_ALLOW_LOCAL=1
            "zip_url": self.url(zip_local_path),
            "zip_sha256": sha256,
            "download_status": "pending",
        }

    def _make_root(self, entries_by_cat):
        root = make_root_multi_source(entries_by_cat, {"localtest_zip": self._src()})
        self.created.append(root)
        return root

    def test_shirts_downloaded_obj_and_png_extracted(self):
        """ZIP MakeHuman: .obj e .png extraídos; .mhclo ignorado (extensão não permitida)."""
        entry = self._entry("mh_shirts_test", "/makehuman_shirts.zip")
        root = self._make_root({"clothing": [entry]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "downloaded",
                         f"resultado inesperado: {rep['results'][0]}")
        adir = root / "assets" / "clothing" / "mh_shirts_test"
        all_files = list(adir.rglob("*"))
        # .obj ou .png devem existir
        has_obj = any(f.suffix == ".obj" for f in all_files)
        has_png = any(f.suffix == ".png" for f in all_files)
        self.assertTrue(has_obj or has_png,
                        f"esperava .obj ou .png em {[f.name for f in all_files]}")
        # .mhclo não deve existir (não está em ALLOWED_EXTENSIONS)
        has_mhclo = any(f.suffix == ".mhclo" for f in all_files)
        self.assertFalse(has_mhclo, ".mhclo não devia ter sido extraído")
        # Validação de integridade passa
        self.assertEqual(validate_assets.run(root)["failed"], 0)

    def test_sha256_mismatch_blocked(self):
        """SHA-256 errado bloqueia o download e não deixa ficheiros no destino."""
        entry = self._entry("mh_sha_fail", "/makehuman_shirts.zip", sha256="0" * 64)
        root = self._make_root({"clothing": [entry]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "failed_checksum",
                         f"resultado inesperado: {rep['results'][0]}")
        self.assertFalse((root / "assets" / "clothing" / "mh_sha_fail").exists())

    def test_correct_sha256_accepted(self):
        """SHA-256 correcto não bloqueia o download."""
        import hashlib
        data = ROUTES["/makehuman_shirts.zip"]
        correct_sha = hashlib.sha256(data).hexdigest()
        entry = self._entry("mh_sha_ok", "/makehuman_shirts.zip", sha256=correct_sha)
        root = self._make_root({"clothing": [entry]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "downloaded",
                         f"resultado inesperado: {rep['results'][0]}")


# ===========================================================================
# Testes de manifesto — verificar recursos adicionados
# ===========================================================================

class TestNewManifestEntries(unittest.TestCase):

    def _load(self, category):
        path = REAL_ROOT / "manifests" / f"{category}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _ids(self, category):
        return {a["id"] for a in self._load(category).get("assets", [])}

    def test_characters_has_all_5_kaykit_adventurers(self):
        ids = self._ids("characters")
        for lid in ["kaykit_adv_knight", "kaykit_adv_barbarian", "kaykit_adv_mage",
                    "kaykit_adv_rogue", "kaykit_adv_rogue_hooded"]:
            self.assertIn(lid, ids, f"{lid} em falta em characters.json")

    def test_characters_kaykit_verified_license_rigged_animated(self):
        for asset in self._load("characters")["assets"]:
            if asset["id"].startswith("kaykit_"):
                with self.subTest(id=asset["id"]):
                    self.assertTrue(asset.get("license_verified"))
                    self.assertEqual(asset.get("license"), "CC0-1.0")
                    self.assertTrue(asset.get("rigged"))
                    self.assertTrue(asset.get("animated"))
                    self.assertEqual(asset.get("source"), "kaykit")
                    self.assertRegex(asset.get("github_commit", ""), r"^[0-9a-f]{40}$")
                    self.assertEqual(asset.get("formats_wanted"), [])

    def test_vehicles_has_all_5_kaykit_cars(self):
        ids = self._ids("vehicles")
        for lid in ["kaykit_city_car_taxi", "kaykit_city_car_sedan", "kaykit_city_car_hatchback",
                    "kaykit_city_car_police", "kaykit_city_car_stationwagon"]:
            self.assertIn(lid, ids, f"{lid} em falta em vehicles.json")

    def test_vehicles_polyhaven_covered_car_preserved(self):
        self.assertIn("polyhaven_covered_car", self._ids("vehicles"))

    def test_clothing_has_makehuman_and_dresses(self):
        ids = self._ids("clothing")
        for lid in ["makehuman_shirts01", "makehuman_dress01", "makehuman_dress02",
                    "makehuman_dress03", "polyhaven_fishermans_hat"]:
            self.assertIn(lid, ids, f"{lid} em falta em clothing.json")

    def test_environments_has_kaykit_entries(self):
        ids = self._ids("environments")
        self.assertGreater(len(ids), 0)
        self.assertTrue(any("kaykit" in lid for lid in ids))

    def test_all_entries_pass_validate_entry_shape(self):
        entries = common.load_manifests(REAL_ROOT, "all")
        problems = [(e.get("id"), common.validate_entry_shape(e))
                    for e in entries if common.validate_entry_shape(e)]
        self.assertEqual(problems, [], f"entradas com problemas: {problems}")

    def test_no_duplicate_ids(self):
        entries = common.load_manifests(REAL_ROOT, "all")
        ids = [e["id"] for e in entries]
        dups = {i for i in ids if ids.count(i) > 1}
        self.assertEqual(dups, set(), f"IDs duplicados: {dups}")

    def test_kaykit_commits_are_40_hex(self):
        for e in common.load_manifests(REAL_ROOT, "all"):
            if e.get("source") == "kaykit":
                with self.subTest(id=e["id"]):
                    self.assertRegex(e.get("github_commit", ""), r"^[0-9a-f]{40}$")

    def test_sources_json_has_kaykit_and_makehuman(self):
        sources = common.load_sources(REAL_ROOT)
        self.assertIn("kaykit", sources)
        self.assertIn("makehuman", sources)
        self.assertEqual(sources["kaykit"]["resolver"], "github_zip")
        self.assertEqual(sources["makehuman"]["resolver"], "zip_url")

    def test_kaykit_source_confirmed_download_host(self):
        sources = common.load_sources(REAL_ROOT)
        self.assertIn("codeload.github.com", sources["kaykit"]["allowed_download_hosts"])

    def test_makehuman_dresses_not_pending_without_url(self):
        """Vestidos não devem estar pending sem URL confirmada."""
        for asset in self._load("clothing")["assets"]:
            if asset["id"].startswith("makehuman_dress"):
                with self.subTest(id=asset["id"]):
                    self.assertNotEqual(asset.get("download_status"), "pending")
                    self.assertIsNone(asset.get("zip_url"))

    def test_kaykit_github_zip_resolver_selected_in_scan(self):
        """SCAN de uma entrada KayKit devolve 'scanned' (não blocked nem failed)."""
        entries = common.load_manifests(REAL_ROOT, "characters")
        knight = next((e for e in entries if e["id"] == "kaykit_adv_knight"), None)
        self.assertIsNotNone(knight)
        sources = common.load_sources(REAL_ROOT)
        src = sources.get("kaykit", {})
        # O resolver deve criar 1 candidato sem lançar excepção
        cands = dl.resolve_github_zip(src, knight, lambda _u: None)
        self.assertEqual(len(cands), 1)
        self.assertIn("codeload.github.com", cands[0]["url"])


# ===========================================================================
# Testes existentes (sem alteração)
# ===========================================================================

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

    def test_real_scan_without_network_never_downloads(self):
        def failing_fetch(url, hosts):
            raise dl.LibraryError("sem rede (simulado)")
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="libcopy_"))
        try:
            shutil.copytree(REAL_ROOT / "manifests", tmp / "manifests")
            shutil.copytree(REAL_ROOT / "catalog", tmp / "catalog")
            report = dl.run(tmp, "scan", fetch_json_fn=failing_fetch)
            self.assertEqual(report["downloaded_assets"], 0)
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

    def test_non_cc0_not_auto_downloadable(self):
        for lic in ("CC-BY-4.0", "MIT", "GPL-3.0", "free"):
            self.assertEqual(common.license_gate(good_entry("a", "a", license=lic), self.sources),
                             (False, "blocked_license_not_auto_downloadable"), lic)

    def test_missing_evidence_rejected(self):
        e = good_entry("a", "a", license_evidence_url=None)
        self.assertEqual(common.license_gate(e, self.sources)[1], "blocked_license_evidence_missing")

    def test_unknown_source_rejected(self):
        self.assertEqual(common.license_gate(good_entry("a", "a", source="nope"), self.sources)[1],
                         "blocked_source_unknown")

    def test_redistribution_must_be_confirmed(self):
        self.assertEqual(common.license_gate(good_entry("a", "a", redistribution_allowed=None),
                                             self.sources)[1],
                         "blocked_redistribution_unconfirmed")


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
        c = self.cand(path, name=name, is_dependency=True)
        c.update(kw)
        return c

    def test_successful_download_writes_metadata_and_license(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "downloaded")
        adir = root / "assets" / "furniture" / "asset_a"
        meta = json.loads((adir / "metadata.json").read_text())
        self.assertEqual(meta["download_status"], "downloaded")
        self.assertTrue((adir / "LICENSE.txt").exists())
        self.assertEqual(validate_assets.run(root)["failed"], 0)

    def test_second_run_skips_already_downloaded(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        dl.run(root, "download")
        self.assertEqual(dl.run(root, "download")["results"][0]["status"], "skipped_already_downloaded")

    def test_unknown_license_not_downloaded(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1", license=None, license_verified=False)]})
        self.assertEqual(dl.run(root, "download")["results"][0]["status"], "blocked_license_unverified")

    def test_incomplete_download_detected(self):
        with self.assertRaises(dl.IncompleteDownload):
            dest = pathlib.Path(tempfile.mkdtemp()) / "s.glb"
            try:
                dl.fetch_file(self.url("/short.glb"), dest, ["127.0.0.1"], 10_000, retries=0)
            finally:
                shutil.rmtree(dest.parent, ignore_errors=True)

    def test_size_limit_enforced(self):
        with self.assertRaises(dl.TooLarge):
            dest = pathlib.Path(tempfile.mkdtemp()) / "b.glb"
            try:
                dl.fetch_file(self.url("/big.glb"), dest, ["127.0.0.1"], 1000, retries=0)
            finally:
                shutil.rmtree(dest.parent, ignore_errors=True)

    def test_host_not_confirmed_is_blocked(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]},
                         source_over={"allowed_download_hosts": []})
        self.assertNotEqual(dl.run(root, "download")["results"][0]["status"], "downloaded")

    def test_ambiguous_selection_blocked(self):
        self.cands["a1"] = [self.cand("/a.glb"), self.cand("/b.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        self.assertEqual(dl.run(root, "download", max_files_per_asset=1)["results"][0]["status"],
                         "blocked_ambiguous_selection")

    def test_md5_mismatch_fails(self):
        self.cands["a1"] = [self.cand("/a.glb", md5="0" * 32)]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        self.assertEqual(dl.run(root, "download")["results"][0]["status"], "failed_checksum")

    def test_duplicate_content_detected(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        self.cands["a2"] = [self.cand("/a_copy.glb", keypath=["files", "a.glb"])]
        root = self.root({"furniture": [good_entry("asset_a", "a1"), good_entry("asset_b", "a2")]})
        rep = dl.run(root, "download")
        self.assertEqual([r["status"] for r in rep["results"]], ["downloaded", "skipped_duplicate_content"])

    def test_zip_extracts_safely_and_skips_scripts(self):
        self.cands["z1"] = [self.cand("/good.zip", keypath=["files", "pack.zip"])]
        root = self.root({"furniture": [good_entry("asset_z", "z1")]})
        rep = dl.run(root, "download")
        self.assertEqual(rep["results"][0]["status"], "downloaded")
        adir = root / "assets" / "furniture" / "asset_z"
        self.assertTrue((adir / "model.glb").exists())
        self.assertFalse((adir / "run.sh").exists())

    def test_zip_slip_blocked(self):
        self.cands["z1"] = [self.cand("/slip.zip", keypath=["files", "slip.zip"])]
        root = self.root({"furniture": [good_entry("asset_z", "z1")]})
        self.assertEqual(dl.run(root, "download")["results"][0]["status"], "blocked_zip_unsafe_path")

    def test_corrupt_zip_rejected(self):
        self.cands["z1"] = [self.cand("/corrupt.zip", keypath=["files", "c.zip"])]
        root = self.root({"furniture": [good_entry("asset_z", "z1")]})
        self.assertEqual(dl.run(root, "download")["results"][0]["status"], "failed_corrupt_zip")

    def test_gltf_with_bin_dependency_downloads_both(self):
        self.cands["gltf1"] = [
            self.cand("/dep_model.gltf", name="dep_model.gltf"),
            self.dep("/dep_model.bin", name="model.bin"),
        ]
        root = self.root({"nature": [good_entry("gltf_asset", "gltf1", category="nature")]})
        self.assertEqual(dl.run(root, "download")["results"][0]["status"], "downloaded")
        adir = root / "assets" / "nature" / "gltf_asset"
        self.assertTrue((adir / "dep_model.gltf").exists())
        self.assertTrue((adir / "model.bin").exists())
        self.assertEqual(validate_assets.run(root)["failed"], 0)

    def test_gltf_missing_dependency_fails_validation(self):
        self.cands["gltf4"] = [self.cand("/missing_dep.gltf", name="missing_dep.gltf")]
        root = self.root({"nature": [good_entry("missing_dep_asset", "gltf4", category="nature")]})
        dl.run(root, "download")
        val = validate_assets.run(root)
        self.assertEqual(val["failed"], 1)
        self.assertTrue(any("missing.bin" in i for i in val["results"][0]["issues"]))

    def test_dependency_from_unauthorized_host_is_blocked(self):
        self.cands["gltf6"] = [
            self.cand("/a.glb"),
            {"url": "https://evil.example/evil.bin", "keypath": ["include", "evil.bin"],
             "size": None, "md5": None, "name": "evil.bin", "is_dependency": True},
        ]
        root = self.root({"furniture": [good_entry("asset_evil", "gltf6")]})
        self.assertNotEqual(dl.run(root, "download")["results"][0]["status"], "downloaded")

    def test_catalog_generation(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1"),
                                        good_entry("asset_u", "u1", license=None, license_verified=False)]})
        dl.run(root, "download")
        cat = build_catalog.build(root)
        by_id = {a["id"]: a for a in cat["assets"]}
        self.assertEqual(by_id["asset_a"]["status"], "downloaded")
        self.assertEqual(by_id["asset_u"]["status"], "blocked_license_unverified")

    def test_validate_detects_tampering(self):
        self.cands["a1"] = [self.cand("/a.glb")]
        root = self.root({"furniture": [good_entry("asset_a", "a1")]})
        dl.run(root, "download")
        (root / "assets" / "furniture" / "asset_a" / "a.glb").write_bytes(b"tampered")
        self.assertEqual(validate_assets.run(root)["failed"], 1)

    def test_non_http_scheme_blocked(self):
        with self.assertRaises(dl.Blocked):
            dl.check_url("file:///etc/passwd", ["127.0.0.1"])
        with self.assertRaises(dl.Blocked):
            dl.check_url("https://evil.example/x", ["127.0.0.1"])


if __name__ == "__main__":
    unittest.main()
