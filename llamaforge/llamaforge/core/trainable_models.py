from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from .net import request_json, open_url

HF_API = "https://huggingface.co/api"


def portable_training_models_root(app_root: str | Path) -> Path:
    """Return the sibling model folder one directory above LlamaForge."""
    return Path(app_root).expanduser().resolve().parent / "LlamaForgeModels"


@dataclass
class TrainableModelInfo:
    repo_id: str
    trainable: bool
    reason: str = ""
    downloads: int = 0
    likes: int = 0
    updated: str = ""
    pipeline_tag: str = ""
    library_name: str = ""
    architecture: str = ""
    gated: bool = False
    private: bool = False
    weight_bytes: int = 0
    total_bytes: int = 0
    has_safetensors: bool = False
    has_config: bool = False
    has_tokenizer: bool = False
    requires_remote_code: bool = False
    base_model: str = ""
    has_adapter_config: bool = False
    has_adapter_weights: bool = False
    checkpoint_kind: str = "full_model"
    tags: list[str] | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["weight_gb"] = round(self.weight_bytes / (1024**3), 2) if self.weight_bytes else 0.0
        d["total_gb"] = round(self.total_bytes / (1024**3), 2) if self.total_bytes else 0.0
        return d


class TrainableModelManager:
    """Hugging Face Transformers checkpoint browser/downloader.

    This intentionally excludes GGUF-only repositories. A trainable checkpoint
    must expose a Transformers config and model weights (preferably safetensors).
    No remote repository Python code is executed by this manager.
    """

    def __init__(self, root: str | Path, token: str = "", log: Callable[[str], None] | None = None):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = token or ""
        self.log = log or (lambda _msg: None)

    @property
    def headers(self) -> dict[str, str]:
        h = {"User-Agent": "LlamaForge-Brain/0.34.2-diagnostics", "Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    @staticmethod
    def local_dir_for(root: Path, repo_id: str) -> Path:
        safe = repo_id.strip().replace("/", "--").replace("\\", "--")
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", safe)
        return root / safe

    def repo_local_dir(self, repo_id: str) -> Path:
        return self.local_dir_for(self.root, repo_id)



    def path_is_managed(self, path: str | Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.root.resolve())
            return True
        except Exception:
            return False

    @staticmethod
    def _path_is_under(path: str | Path, root: str | Path) -> bool:
        try:
            Path(path).resolve().relative_to(Path(root).resolve())
            return True
        except Exception:
            return False

    def relocate_training_source(
        self,
        source_path: str | Path,
        *,
        legacy_root: str | Path | None = None,
        progress: Callable[[str, float, int, int], None] | None = None,
    ) -> str:
        """Move an older LlamaForge-managed checkpoint into this manager's root.

        Only paths under ``legacy_root`` are relocated automatically; arbitrary
        manually selected folders are never moved behind the user's back. PEFT
        training bundles move both the source adapter and its underlying base and
        rewrite the bundle manifest to the new portable paths.
        """
        src = Path(source_path).expanduser()
        if not src.is_dir():
            return str(src)
        if self.path_is_managed(src):
            return str(src)
        if legacy_root is None or not self._path_is_under(src, legacy_root):
            return str(src)

        def repo_for(p: Path) -> str:
            repo = self.source_repo_for_local(p)
            if repo:
                return repo
            meta = p / '.llamaforge-source.json'
            try:
                if meta.is_file():
                    return str(json.loads(meta.read_text(encoding='utf-8')).get('repo_id') or '')
            except Exception:
                pass
            return ''

        bundle_data = None
        bundle_file = src / '.llamaforge-bundle.json'
        if bundle_file.is_file():
            try:
                data = json.loads(bundle_file.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    bundle_data = data
            except Exception:
                bundle_data = None

        jobs: list[tuple[Path, str, str]] = []
        if bundle_data:
            under_path = Path(str(bundle_data.get('underlying_local_dir') or '')).expanduser()
            under_repo = str(bundle_data.get('underlying_repo') or '').strip() or repo_for(under_path)
            if under_path.is_dir() and under_repo and self._path_is_under(under_path, legacy_root):
                jobs.append((under_path, under_repo, 'Underlying base'))
        source_repo = str((bundle_data or {}).get('source_repo') or '').strip() or repo_for(src)
        if not source_repo:
            raise RuntimeError(f'Cannot relocate legacy training checkpoint because its source repo is unknown: {src}')
        jobs.append((src, source_repo, 'Training source'))

        total_bytes = 0
        for old, _repo, _label in jobs:
            try:
                total_bytes += sum(f.stat().st_size for f in old.rglob('*') if f.is_file())
            except Exception:
                pass
        moved_bytes = 0
        new_paths: dict[str, Path] = {}

        for old, repo, label in jobs:
            dest = self.repo_local_dir(repo)
            new_paths[repo] = dest
            if old.resolve() == dest.resolve():
                continue
            if dest.exists():
                if self.local_checkpoint_ready(dest):
                    self.log(f'[brain:storage] portable checkpoint already exists repo={repo} path={dest}')
                    # The source is an older LlamaForge-managed duplicate under
                    # legacy_root. Once the portable copy verifies, remove the
                    # hidden duplicate instead of silently wasting disk space.
                    if old.exists() and old.resolve() != dest.resolve() and self._path_is_under(old, legacy_root):
                        shutil.rmtree(old, ignore_errors=True)
                        self.log(f'[brain:storage] removed verified legacy duplicate path={old}')
                    continue
                raise RuntimeError(f'Portable training destination already exists but is incomplete: {dest}')
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.log(f'[brain:storage] moving {label.lower()} repo={repo} from={old} to={dest}')
            # Same-volume rename is effectively instant and does not duplicate a
            # multi-GB checkpoint. Cross-volume moves fall back to shutil.move.
            try:
                same_volume = (old.drive.lower() == dest.drive.lower()) if os.name == 'nt' else (old.stat().st_dev == dest.parent.stat().st_dev)
            except Exception:
                same_volume = False
            size = 0
            try:
                size = sum(f.stat().st_size for f in old.rglob('*') if f.is_file())
            except Exception:
                pass
            if same_volume:
                old.replace(dest)
            else:
                free = shutil.disk_usage(dest.parent).free
                if size and free < size + 512 * 1024**2:
                    raise RuntimeError(f'Not enough free space to move the training model beside LlamaForge. Need about {size/(1024**3):.2f} GB plus 0.5 GB reserve.')
                shutil.move(str(old), str(dest))
            moved_bytes += size
            if progress:
                progress(f'Moving {label} beside LlamaForge', moved_bytes / total_bytes if total_bytes else 1.0, moved_bytes, total_bytes)

        source_dest = self.repo_local_dir(source_repo)
        if bundle_data:
            under_repo = str(bundle_data.get('underlying_repo') or '').strip()
            if under_repo:
                bundle_data['underlying_local_dir'] = str(self.repo_local_dir(under_repo))
            bundle_data['source_local_dir'] = str(source_dest)
            bundle_data['portable_storage_root'] = str(self.root)
            bundle_data['relocated_at'] = time.time()
            (source_dest / '.llamaforge-bundle.json').write_text(json.dumps(bundle_data, ensure_ascii=False, indent=2), encoding='utf-8')
        if not self.local_checkpoint_ready(source_dest):
            raise RuntimeError(f'Relocated training source failed verification: {source_dest}')
        self.log(f'[brain:storage] relocation complete source={source_dest}')
        return str(source_dest)

    def _iter_local_checkpoint_dirs(self, extra_roots: list[str] | None = None):
        """Yield complete local Transformers/PEFT checkpoints from managed and user roots.

        Model folders configured in the normal Library may point either at a
        checkpoint itself or at a parent such as ``LlamaForgeModels`` / LM Studio.
        Older builds only inspected direct children of ``self.root`` so valid
        downloaded Safetensors checkpoints disappeared when users added that same
        folder as a model path.  Scan lightweight metadata names only; never open
        the multi-GB weight files during discovery.
        """
        roots: list[Path] = [self.root]
        for raw in extra_roots or []:
            try:
                p = Path(os.path.expanduser(str(raw))).resolve()
            except Exception:
                continue
            if p.exists() and p.is_dir():
                roots.append(p)

        seen: set[str] = set()
        seen_roots: set[str] = set()
        skip_names = {'.git', '__pycache__', 'node_modules', 'blobs', 'refs'}
        for root in roots:
            try:
                root = root.resolve()
            except Exception:
                pass
            root_key = os.path.normcase(str(root))
            if root_key in seen_roots:
                continue
            seen_roots.add(root_key)
            # A configured path may be the checkpoint directory itself.
            if self.local_checkpoint_ready(root):
                key = os.path.normcase(str(root))
                if key not in seen:
                    seen.add(key); yield root
                continue

            # The managed root convention is one checkpoint per direct child.
            # User model folders can be nested (publisher/model, HF snapshots), so
            # inspect a bounded metadata tree. Weight contents are never read.
            try:
                walker = os.walk(root)
            except Exception:
                continue
            for current, dirs, files in walker:
                cur = Path(current)
                try:
                    depth = len(cur.relative_to(root).parts)
                except Exception:
                    depth = 0
                dirs[:] = [d for d in dirs if d not in skip_names and not d.startswith('.git')]
                if depth >= 5:
                    dirs[:] = []
                names = {str(x).lower() for x in files}
                if not ({'config.json', 'adapter_config.json'} & names):
                    continue
                if not self.local_checkpoint_ready(cur):
                    continue
                try:
                    resolved = cur.resolve()
                except Exception:
                    resolved = cur
                key = os.path.normcase(str(resolved))
                if key in seen:
                    dirs[:] = []
                    continue
                seen.add(key)
                yield resolved
                # A model checkpoint should not contain another independent model
                # under itself in the common layouts we support; pruning prevents
                # needless traversal of caches and tokenizer assets.
                dirs[:] = []

    @staticmethod
    def _repo_id_from_local_path(d: Path) -> str:
        """Recover a human repository id from managed folders and HF snapshots."""
        meta=d/".llamaforge-source.json"
        try:
            if meta.is_file():
                repo=str(json.loads(meta.read_text(encoding="utf-8")).get("repo_id") or "").strip()
                if repo:
                    return repo
        except Exception:
            pass
        # Hugging Face cache layout: .../hub/models--org--repo/snapshots/<hash>
        for part in reversed(d.parts):
            if part.startswith("models--"):
                bits=part[len("models--"):].split("--")
                if len(bits)>=2:
                    return bits[0]+"/"+"--".join(bits[1:])
        return d.name.replace("--","/",1) if "--" in d.name else d.name

    @staticmethod
    def _local_model_row(d: Path) -> dict:
        repo_id=TrainableModelManager._repo_id_from_local_path(d)
        cfg={}
        try:
            cp=d/'config.json'
            if cp.is_file():
                raw=json.loads(cp.read_text(encoding='utf-8'))
                if isinstance(raw,dict): cfg=raw
        except Exception:
            cfg={}
        weights=list(d.rglob("*.safetensors"))+list(d.rglob("*.bin"))
        adapter=(d/'adapter_config.json').is_file() and any(x.name.lower().startswith('adapter_model.') for x in weights)
        arch=str(cfg.get('model_type') or '')
        if not arch:
            arches=cfg.get('architectures') or []
            if isinstance(arches,list) and arches: arch=str(arches[0])
        return {
            "repo_id":repo_id, "local_dir":str(d), "downloaded":True, "trainable":True,
            "reason":"Downloaded PEFT adapter checkpoint" if adapter else "Downloaded trainable checkpoint",
            "weight_gb":round(sum(x.stat().st_size for x in weights)/(1024**3),2),
            "architecture":arch, "has_safetensors":any(x.suffix.lower()=='.safetensors' for x in weights),
            "checkpoint_kind":"peft_adapter" if adapter else "full_model",
        }

    def local_models(self, extra_roots: list[str] | None = None) -> list[dict]:
        rows=[self._local_model_row(d) for d in self._iter_local_checkpoint_dirs(extra_roots)]
        # Mark low-level base checkpoints pulled only to support a PEFT source.
        # They remain visible to diagnostics, but the normal model picker should
        # not present them as a second independent model the user has to choose.
        dependencies: dict[str, str] = {}
        for row in rows:
            src=Path(str(row.get('local_dir') or ''))
            bundle=src/'.llamaforge-bundle.json'
            try:
                if bundle.is_file():
                    data=json.loads(bundle.read_text(encoding='utf-8'))
                    under=str(Path(str(data.get('underlying_local_dir') or '')).resolve())
                    if under:
                        dependencies[os.path.normcase(under)]=str(row.get('repo_id') or '')
                    row['underlying_repo']=str(data.get('underlying_repo') or '')
                    row['bundle_ready']=bool(under and Path(under).is_dir())
            except Exception:
                pass
        for row in rows:
            try:
                key=os.path.normcase(str(Path(str(row.get('local_dir') or '')).resolve()))
            except Exception:
                key=os.path.normcase(str(row.get('local_dir') or ''))
            owner=dependencies.get(key,'')
            row['internal_dependency']=bool(owner)
            row['dependency_of']=owner

        # The same HF repo can be discovered through both ~/.cache/huggingface
        # and LlamaForge's portable store. Keep one logical row and prefer the
        # portable managed copy because it contains our verification manifests.
        best: dict[str, dict] = {}
        extras: list[dict] = []
        for row in rows:
            repo=str(row.get('repo_id') or '').strip().lower()
            key=repo or os.path.normcase(str(row.get('local_dir') or ''))
            prev=best.get(key)
            if prev is None:
                best[key]=row; continue
            cur_managed=self.path_is_managed(str(row.get('local_dir') or ''))
            prev_managed=self.path_is_managed(str(prev.get('local_dir') or ''))
            if cur_managed and not prev_managed:
                best[key]=row
        return sorted(best.values(),key=lambda r:str(r.get('repo_id') or r.get('local_dir') or '').lower())

    def _model_data(self, repo_id: str) -> dict:
        params = urllib.parse.urlencode({"files_metadata": "true"})
        _, data = request_json(
            f"{HF_API}/models/{urllib.parse.quote(repo_id, safe='/')}?{params}",
            headers=self.headers,
            timeout=40,
        )
        if not isinstance(data, dict):
            raise RuntimeError("Hugging Face returned an invalid model record")
        return data

    @staticmethod
    def _file_size(row: dict) -> int:
        try:
            return int(row.get("size") or (row.get("lfs") or {}).get("size") or 0)
        except Exception:
            return 0

    @classmethod
    def assess_data(cls, data: dict) -> TrainableModelInfo:
        repo_id = str(data.get("id") or data.get("modelId") or "")
        siblings = list(data.get("siblings") or [])
        names = [str(x.get("rfilename") or "") for x in siblings if isinstance(x, dict)]
        sizes = {str(x.get("rfilename") or ""): cls._file_size(x) for x in siblings if isinstance(x, dict)}
        lower = [n.lower() for n in names]
        has_config = "config.json" in lower
        safe = [n for n in names if n.lower().endswith(".safetensors") and "adapter_model" not in n.lower()]
        bins = [n for n in names if re.search(r"(?:pytorch_model|model).*\.bin$", n.lower())]
        ggufs = [n for n in names if n.lower().endswith(".gguf")]
        tok_patterns = (
            "tokenizer.json", "tokenizer.model", "tokenizer_config.json", "spiece.model",
            "sentencepiece.model", "vocab.json", "merges.txt"
        )
        has_tokenizer = any(n.lower() in tok_patterns or n.lower().startswith("tokenizer") for n in names)
        adapter_cfg = any(n.lower() == "adapter_config.json" for n in names)
        adapter_weights = [n for n in names if n.lower() in ("adapter_model.safetensors", "adapter_model.bin")]
        has_weights = bool(safe or bins)
        config = data.get("config") if isinstance(data.get("config"), dict) else {}
        arch = str(config.get("model_type") or "")
        architectures = config.get("architectures") or []
        if not arch and isinstance(architectures, list) and architectures:
            arch = str(architectures[0])
        auto_map = config.get("auto_map")
        requires_remote_code = bool(auto_map)
        card = data.get("cardData") if isinstance(data.get("cardData"), dict) else {}
        base_model = card.get("base_model") or data.get("baseModels") or ""
        if isinstance(base_model, list):
            base_model = next((str(x) for x in base_model if isinstance(x, str) and x.strip()), "")
        tags = [str(x) for x in (data.get("tags") or []) if isinstance(x, str)]
        pipeline = str(data.get("pipeline_tag") or "").strip().lower()
        total_bytes = sum(sizes.values())
        gguf_only = bool(ggufs) and not has_weights
        adapter_checkpoint = bool(adapter_cfg and adapter_weights)
        # Report the bytes LlamaForge will actually take from this repository.
        # A PEFT source can also contain merged/full-model weights many GB in
        # size, but the Brain bundle intentionally downloads only its adapter
        # and then resolves the declared underlying base separately.
        weight_bytes = sum(sizes.get(n, 0) for n in (adapter_weights if adapter_checkpoint else (safe or bins)))
        # The Brain trainer is a causal/generative language-model trainer, not a
        # generic Transformers fine-tuner. Older builds marked any checkpoint
        # with config+weights as trainable, so BERT/classifiers/vision models
        # could appear in the catalog and then fail only after a large download.
        blocked_pipelines = {
            "fill-mask", "feature-extraction", "text-classification", "token-classification",
            "sentence-similarity", "image-classification", "object-detection", "text-to-image",
            "image-to-image", "audio-classification", "automatic-speech-recognition",
        }
        arch_low = " ".join([arch] + [str(x) for x in architectures]).lower()
        generative_hint = (
            pipeline in {"text-generation", "image-text-to-text", "text2text-generation"}
            or "causallm" in arch_low or "conditionalgeneration" in arch_low
            or any(t.lower() in {"text-generation", "conversational"} for t in tags)
        )
        structurally_ready = bool((has_config and has_weights) or adapter_checkpoint)
        compatible = bool(pipeline not in blocked_pipelines and (generative_hint or not pipeline))
        # Full checkpoints need a tokenizer locally. PEFT sources may inherit it
        # from the declared base and are validated again when the bundle is built.
        tokenizer_ok = bool(has_tokenizer or adapter_checkpoint)
        trainable = bool(structurally_ready and compatible and tokenizer_ok)
        checkpoint_kind = "peft_adapter" if adapter_checkpoint else "full_model"
        reason = ("PEFT adapter source; LlamaForge will load its declared base and stack a Personal Brain LoRA" if trainable and adapter_checkpoint else
                  "Ready for Transformers/PEFT training" if trainable else
                  "GGUF-only repository; choose the original Transformers checkpoint" if gguf_only else
                  f"Unsupported model task for Personal Brain: {pipeline}" if structurally_ready and not compatible else
                  "Tokenizer files are missing; this checkpoint cannot be trained by the chat Brain" if structurally_ready and not tokenizer_ok else
                  "Missing config.json/model weights or a valid PEFT adapter checkpoint")
        return TrainableModelInfo(
            repo_id=repo_id,
            trainable=trainable,
            reason=reason,
            downloads=int(data.get("downloads") or 0),
            likes=int(data.get("likes") or 0),
            updated=str(data.get("lastModified") or ""),
            pipeline_tag=str(data.get("pipeline_tag") or ""),
            library_name=str(data.get("library_name") or ""),
            architecture=arch,
            gated=bool(data.get("gated") not in (None, False, "false", "False")),
            private=bool(data.get("private")),
            weight_bytes=weight_bytes,
            total_bytes=total_bytes,
            has_safetensors=bool(safe),
            has_config=has_config,
            has_tokenizer=has_tokenizer,
            requires_remote_code=requires_remote_code,
            base_model=str(base_model or ""),
            has_adapter_config=adapter_cfg,
            has_adapter_weights=bool(adapter_weights),
            checkpoint_kind=checkpoint_kind,
            tags=tags,
        )

    def inspect(self, repo_id: str) -> dict:
        data = self._model_data(repo_id)
        info = self.assess_data(data)
        result = info.to_dict()
        result["local_dir"] = str(self.repo_local_dir(repo_id))
        result["downloaded"] = self.local_checkpoint_ready(self.repo_local_dir(repo_id))
        return result

    def search(self, query: str, limit: int = 20) -> list[dict]:
        query = str(query or "").strip()
        params = {"sort": "downloads", "direction": -1, "limit": max(1, min(50, int(limit))), "full": "true"}
        if query:
            params["search"] = query
        _, data = request_json(f"{HF_API}/models?{urllib.parse.urlencode(params)}", headers=self.headers, timeout=35)
        if not isinstance(data, list):
            return []
        out: list[dict] = []
        candidates=[raw for raw in data[: max(1, min(30, int(limit)))] if isinstance(raw,dict) and (raw.get("id") or raw.get("modelId"))]
        def inspect_one(raw):
            repo=str(raw.get("id") or raw.get("modelId") or "")
            try:
                detailed = raw if raw.get("siblings") and raw.get("config") else self._model_data(repo)
                info = self.assess_data(detailed)
                row = info.to_dict(); row["downloaded"] = self.local_checkpoint_ready(self.repo_local_dir(repo)); row["local_dir"] = str(self.repo_local_dir(repo)); return row
            except Exception as exc:
                return {"repo_id": repo, "trainable": False, "reason": f"Could not inspect: {exc}", "downloads": int(raw.get("downloads") or 0), "likes": int(raw.get("likes") or 0), "downloaded": False}
        with ThreadPoolExecutor(max_workers=min(6,max(1,len(candidates)))) as pool:
            futures=[pool.submit(inspect_one,raw) for raw in candidates]
            for fut in as_completed(futures):
                out.append(fut.result())
        out.sort(key=lambda r: (not bool(r.get("trainable")), -int(r.get("downloads") or 0)))
        return out

    @staticmethod
    def _wanted_files(data: dict, allow_remote_code: bool = False) -> list[dict]:
        siblings = [x for x in (data.get("siblings") or []) if isinstance(x, dict)]
        names = [str(x.get("rfilename") or "") for x in siblings]
        safe = [n for n in names if n.lower().endswith(".safetensors") and "adapter_model" not in n.lower()]
        bins = [n for n in names if re.search(r"(?:pytorch_model|model).*\.bin$", n.lower())]
        adapter_cfg = next((n for n in names if n.lower() == "adapter_config.json"), "")
        adapter_weights = [n for n in names if n.lower() in ("adapter_model.safetensors", "adapter_model.bin")]
        # Prefer the lightweight PEFT checkpoint when a repository exposes both
        # merged full weights and its original adapter. This avoids downloading
        # tens of GB only to reconstruct the same fine-tune for continual learning.
        weights = set(adapter_weights if adapter_cfg and adapter_weights else (safe or bins))
        required_exact = {
            "config.json", "generation_config.json", "tokenizer.json", "tokenizer.model",
            "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json",
            "vocab.json", "merges.txt", "spiece.model", "sentencepiece.model",
            "adapter_config.json",
        }
        selected: list[dict] = []
        for row in siblings:
            n = str(row.get("rfilename") or "")
            low = n.lower()
            include = n in weights or low in required_exact
            include = include or low.endswith(".safetensors.index.json") or low.endswith("pytorch_model.bin.index.json")
            include = include or low.startswith("tokenizer") or low.startswith("vocab.")
            if allow_remote_code and low.endswith(".py"):
                include = True
            if include:
                selected.append({"name": n, "size": TrainableModelManager._file_size(row)})
        return selected



    @staticmethod
    def is_prequantized_bnb_repo(repo_id: str) -> bool:
        """Return True for repository names that explicitly advertise bnb k-bit weights.

        Pre-quantized Hub checkpoints are excellent inference artifacts, but they
        are a fragile training dependency across Transformers/bitsandbytes
        versions. Personal Brain therefore prefers the equivalent full
        Transformers checkpoint and quantizes it *during* load.
        """
        name=str(repo_id or '').strip().split('/')[-1].lower()
        return bool(re.search(r'(?:^|[-_.])(?:unsloth[-_.])?bnb[-_.]?(?:4|8)bit$', name)
                    or re.search(r'(?:^|[-_.])bitsandbytes[-_.]?(?:4|8)bit$', name))

    @staticmethod
    def dequantized_repo_candidates(repo_id: str) -> list[str]:
        repo=str(repo_id or '').strip()
        if '/' not in repo:
            return []
        org,name=repo.split('/',1)
        candidates=[]
        patterns=(
            r'[-_.]unsloth[-_.]bnb[-_.]?(?:4|8)bit$',
            r'[-_.]bnb[-_.]?(?:4|8)bit$',
            r'[-_.]bitsandbytes[-_.]?(?:4|8)bit$',
        )
        for pat in patterns:
            stripped=re.sub(pat,'',name,flags=re.I).rstrip('-_.')
            if stripped and stripped!=name:
                cand=f'{org}/{stripped}'
                if cand not in candidates:
                    candidates.append(cand)
        return candidates

    @staticmethod
    def local_is_prequantized_bnb(path: str | Path) -> bool:
        p=Path(path)
        try:
            cfg=json.loads((p/'config.json').read_text(encoding='utf-8'))
        except Exception:
            return False
        q=cfg.get('quantization_config')
        if not isinstance(q,dict):
            return False
        method=str(q.get('quant_method') or '').lower()
        return bool('bitsandbytes' in method and (q.get('load_in_4bit') or q.get('_load_in_4bit') or q.get('load_in_8bit') or q.get('_load_in_8bit')))

    def resolve_training_underlying_repo(self, repo_id: str) -> dict:
        """Prefer a full-precision/BF16 sibling over a pre-quantized bnb repo.

        The trainer will quantize the full checkpoint on the fly with the
        currently installed bitsandbytes backend. This avoids serialised
        Params4bit/QuantState compatibility failures such as the Windows CPU
        failure seen with older Unsloth pre-quantized checkpoints.
        """
        declared=str(repo_id or '').strip()
        candidates=self.dequantized_repo_candidates(declared)
        if not candidates:
            return {'repo':declared,'declared_repo':declared,'normalized':False,'reason':'declared-base'}
        errors=[]
        for candidate in candidates:
            try:
                info=self.inspect(candidate)
                if info.get('trainable') and str(info.get('checkpoint_kind') or '')=='full_model':
                    return {
                        'repo':candidate,'declared_repo':declared,'normalized':True,
                        'reason':'dynamic-4bit-from-full-checkpoint',
                    }
                errors.append(str(info.get('reason') or f'{candidate} is not trainable'))
            except Exception as exc:
                errors.append(str(exc))
        return {
            'repo':declared,'declared_repo':declared,'normalized':False,
            'reason':'prequantized-fallback','errors':errors,
        }

    @staticmethod
    def source_repo_for_local(path: str | Path) -> str:
        p=Path(path)/".llamaforge-source.json"
        try:
            if p.is_file(): return str(json.loads(p.read_text(encoding="utf-8")).get("repo_id") or "")
        except Exception: pass
        return ""

    @staticmethod
    def verify_local(path: str | Path) -> dict:
        p=Path(path)
        issues=[]
        adapter_mode=(p/"adapter_config.json").is_file() and ((p/"adapter_model.safetensors").is_file() or (p/"adapter_model.bin").is_file())
        if not adapter_mode and not (p/"config.json").is_file(): issues.append("config.json missing")
        weights=list(p.rglob("*.safetensors"))+list(p.rglob("*.bin"))
        if not weights: issues.append("model/adapter weights missing")
        manifest=p/".llamaforge-source.json"
        repo=""
        if manifest.is_file():
            try:
                data=json.loads(manifest.read_text(encoding="utf-8")); repo=str(data.get("repo_id") or "")
                for row in data.get("files") or []:
                    name=str(row.get("name") or "").replace("\\", "/").lstrip("/")
                    expected=int(row.get("size") or 0)
                    target=p/Path(name)
                    if not target.is_file(): issues.append(f"missing {name}")
                    elif expected and target.stat().st_size!=expected: issues.append(f"size mismatch {name}")
            except Exception as exc: issues.append(f"manifest invalid: {exc}")
        return {"ok":not issues,"issues":issues,"repo_id":repo,"path":str(p),"weight_files":len(weights),"checkpoint_kind":"peft_adapter" if adapter_mode else "full_model"}

    @staticmethod
    def local_checkpoint_ready(path: str | Path) -> bool:
        return bool(TrainableModelManager.verify_local(path).get("ok"))


    def training_bundle_info(self, source_path: str | Path) -> dict:
        p = Path(source_path)
        bundle = p / ".llamaforge-bundle.json"
        if not bundle.is_file():
            return {"ok": False, "source": str(p), "underlying_repo": "", "underlying_local_dir": ""}
        try:
            data = json.loads(bundle.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "source": str(p), "error": str(exc), "underlying_repo": "", "underlying_local_dir": ""}
        under = Path(str(data.get("underlying_local_dir") or ""))
        under_repo=str(data.get("underlying_repo") or "").strip()
        prequantized_alias=self.is_prequantized_bnb_repo(under_repo) or (under.is_dir() and self.local_is_prequantized_bnb(under))
        # Bundles created by older builds may point at a serialized bnb 4-bit
        # dependency. Treat those as needing one automatic rebuild so chat never
        # reports "ready" and then deterministically fails in the trainer.
        ok = bool(under.is_dir() and self.local_checkpoint_ready(under) and not prequantized_alias)
        return {**data, "ok": ok, "source": str(p), "underlying_local_dir": str(under) if str(under) else "",
                "needs_rebuild": bool(prequantized_alias), "prequantized_dependency": bool(prequantized_alias)}

    def download_training_bundle(
        self,
        repo_id: str,
        *,
        allow_remote_code: bool = False,
        progress: Callable[[str, float, int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> str:
        """Download a deterministic local training source.

        For normal full checkpoints this is identical to download_snapshot().
        For PEFT fine-tune repositories we download the light source adapter and
        *also* its declared underlying base. This prevents Transformers from
        silently downloading several GB later while the UI appears stuck.
        """
        info = self.inspect(repo_id)
        kind = str(info.get("checkpoint_kind") or "")
        if kind != "peft_adapter":
            return self.download_snapshot(repo_id, allow_remote_code=allow_remote_code, progress=progress, cancel=cancel)

        # Remember an older managed dependency before refreshing the source
        # adapter. If this bundle is rebuilt to a portable full checkpoint, the
        # obsolete pre-quantized dependency can be deleted *after* the new bundle
        # verifies, recovering several GB without touching arbitrary user folders.
        source_hint=self.repo_local_dir(repo_id)
        old_underlying: Path | None = None
        old_bundle=source_hint/'.llamaforge-bundle.json'
        try:
            if old_bundle.is_file():
                old_data=json.loads(old_bundle.read_text(encoding='utf-8'))
                candidate=Path(str(old_data.get('underlying_local_dir') or '')).expanduser()
                if candidate.is_dir(): old_underlying=candidate
        except Exception:
            old_underlying=None

        def source_progress(msg, pct, done, total):
            if progress:
                progress("Source adapter · " + str(msg), 0.02 + 0.18 * max(0.0, min(1.0, float(pct))), done, total)
        source_local = Path(self.download_snapshot(repo_id, allow_remote_code=allow_remote_code, progress=source_progress, cancel=cancel))
        cfg_path = source_local / "adapter_config.json"
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Downloaded PEFT source has an unreadable adapter_config.json: {exc}") from exc
        task_type=str(cfg.get("task_type") or "").strip().upper()
        if task_type and task_type != "CAUSAL_LM":
            raise RuntimeError(f"This PEFT adapter is for {task_type}, not causal language-model training")
        underlying_repo = str(cfg.get("base_model_name_or_path") or "").strip()
        # Some old adapters save the author's local training path. Fall back to
        # model-card lineage when possible instead of trying to call the Hub API
        # with something like C:\\models\\base or /home/user/base.
        looks_local = (not underlying_repo or underlying_repo.startswith(("/", ".", "~"))
                       or "\\" in underlying_repo or (len(underlying_repo) > 2 and underlying_repo[1:3] == ":/"))
        if looks_local:
            fallback=str(info.get("base_model") or "").strip()
            if fallback and "/" in fallback:
                self.log(f"[brain:download] adapter declared local base={underlying_repo!r}; using model-card base={fallback}")
                underlying_repo=fallback
        if not underlying_repo:
            raise RuntimeError("PEFT source does not declare base_model_name_or_path")
        if "/" not in underlying_repo:
            raise RuntimeError(f"PEFT source declares an invalid Hugging Face base model: {underlying_repo}")
        declared_underlying_repo=underlying_repo
        resolved=self.resolve_training_underlying_repo(underlying_repo)
        underlying_repo=str(resolved.get('repo') or underlying_repo)
        if resolved.get('normalized'):
            self.log(f"[brain:download] training-safe base: {declared_underlying_repo} -> {underlying_repo} (quantize on load)")
        else:
            self.log(f"[brain:download] source adapter={repo_id} underlying_base={underlying_repo}")
        underlying_info = self.inspect(underlying_repo)
        if not underlying_info.get("trainable"):
            raise RuntimeError(underlying_info.get("reason") or f"Underlying base {underlying_repo} is not downloadable as a trainable checkpoint")

        def base_progress(msg, pct, done, total):
            if progress:
                progress("Underlying base · " + str(msg), 0.20 + 0.78 * max(0.0, min(1.0, float(pct))), done, total)
        underlying_local = Path(self.download_snapshot(underlying_repo, allow_remote_code=allow_remote_code, progress=base_progress, cancel=cancel))
        bundle = {
            "source_repo": repo_id,
            "source_local_dir": str(source_local),
            "declared_underlying_repo": declared_underlying_repo,
            "underlying_repo": underlying_repo,
            "underlying_local_dir": str(underlying_local),
            "underlying_strategy": str(resolved.get('reason') or 'declared-base'),
            "prepared_at": time.time(),
        }
        (source_local / ".llamaforge-bundle.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        verified=self.training_bundle_info(source_local)
        if not verified.get('ok'):
            raise RuntimeError('The rebuilt training bundle did not pass local verification')
        if old_underlying and old_underlying.is_dir():
            try:
                different=old_underlying.resolve()!=underlying_local.resolve()
            except Exception:
                different=str(old_underlying)!=str(underlying_local)
            if different and self.path_is_managed(old_underlying) and self.local_is_prequantized_bnb(old_underlying):
                try:
                    shutil.rmtree(old_underlying)
                    self.log(f"[brain:storage] removed obsolete pre-quantized dependency {old_underlying}")
                except Exception as exc:
                    self.log(f"[brain:storage] could not remove obsolete dependency {old_underlying}: {exc}")
        if progress:
            progress("Training bundle verified", 1.0, 1, 1)
        self.log(f"[brain:download] training bundle ready source={repo_id} underlying={underlying_repo} path={source_local}")
        return str(source_local)

    def download_snapshot(
        self,
        repo_id: str,
        *,
        allow_remote_code: bool = False,
        progress: Callable[[str, float, int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> str:
        data = self._model_data(repo_id)
        info = self.assess_data(data)
        if not info.trainable:
            raise RuntimeError(info.reason)
        if info.gated and not self.token:
            raise RuntimeError("This Hugging Face model is gated. Add a Hugging Face access token in LlamaForge settings first.")
        if info.requires_remote_code and not allow_remote_code:
            raise RuntimeError("This model requires custom remote Python code. Automatic training blocks remote code for safety; enable it manually only if you trust the repository.")
        files = self._wanted_files(data, allow_remote_code=allow_remote_code)
        if not files:
            raise RuntimeError("No trainable checkpoint files were found in this repository")
        dest = self.repo_local_dir(repo_id)
        dest.mkdir(parents=True, exist_ok=True)
        revision = str(data.get("sha") or "main").strip() or "main"
        manifest = {"repo_id": repo_id, "revision": revision, "downloaded_at": time.time(), "files": files, "architecture": info.architecture}
        total = sum(int(x.get("size") or 0) for x in files)
        if total:
            free = shutil.disk_usage(dest).free
            remaining = 0
            for x in files:
                rel = Path(str(x["name"]).replace("\\", "/").lstrip("/"))
                target = dest / rel
                part = target.with_suffix(target.suffix + ".part")
                have = target.stat().st_size if target.exists() else (part.stat().st_size if part.exists() else 0)
                remaining += max(0, int(x.get("size") or 0) - have)
            reserve = 1024**3
            if free < remaining + reserve:
                raise RuntimeError(f"Not enough disk space for the trainable checkpoint. Need about {remaining/(1024**3):.2f} GB plus 1 GB reserve; only {free/(1024**3):.2f} GB is free.")
        done_base = 0
        for idx, row in enumerate(files, 1):
            if cancel and cancel.is_set():
                raise RuntimeError("Trainable model download cancelled")
            name = row["name"]
            expected = int(row.get("size") or 0)
            rel = Path(str(name).replace("\\", "/").lstrip("/"))
            if ".." in rel.parts:
                raise RuntimeError(f"Unsafe repository path rejected: {name}")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_suffix(target.suffix + ".part")
            # Recover cleanly from an interrupted download that already reached
            # the exact advertised size before the final rename happened.
            if part.exists() and expected:
                part_size = part.stat().st_size
                if part_size == expected:
                    part.replace(target)
                elif part_size > expected:
                    part.unlink(missing_ok=True)
            existing = part.stat().st_size if part.exists() else (target.stat().st_size if target.exists() else 0)
            if target.exists() and expected and target.stat().st_size == expected:
                done_base += expected
                if progress: progress(f"Verified {target.name}", done_base / total if total else idx / len(files), done_base, total)
                continue
            headers = dict(self.headers)
            append = False
            if part.exists() and existing:
                headers["Range"] = f"bytes={existing}-"
            url = f"https://huggingface.co/{urllib.parse.quote(repo_id, safe='/')}/resolve/{urllib.parse.quote(revision, safe='')}/{urllib.parse.quote(name, safe='/')}?download=true"
            req = urllib.request.Request(url, headers=headers, method="GET")
            self.log(f"[brain:download] GET {repo_id}/{name}")
            with open_url(req, timeout=240) as r:
                status = int(getattr(r, "status", 200))
                append = bool(existing and status == 206)
                if not append:
                    existing = 0
                mode = "ab" if append else "wb"
                local_done = existing
                with part.open(mode) as f:
                    while True:
                        if cancel and cancel.is_set():
                            raise RuntimeError("Trainable model download cancelled")
                        chunk = r.read(4 * 1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        local_done += len(chunk)
                        absolute = done_base + local_done
                        if progress:
                            progress(f"Downloading {Path(name).name} ({idx}/{len(files)})", absolute / total if total else idx / len(files), absolute, total)
            part.replace(target)
            if expected and target.stat().st_size != expected:
                raise RuntimeError(f"Downloaded file size mismatch for {target.name}: expected {expected}, got {target.stat().st_size}")
            done_base += expected or target.stat().st_size
        (dest / ".llamaforge-source.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if not self.local_checkpoint_ready(dest):
            raise RuntimeError("Download finished but the local trainable checkpoint failed verification")
        return str(dest)
