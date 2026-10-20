#!/usr/bin/env python3
"""Standalone, standard-library-only entry point for a private reproduction bundle.

Copied into a bundle as tools/bundle.py. No command implicitly trains, opens a
camera, reads the old checkout, or inherits provider credentials.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
METADATA_SUFFIXES = {".json", ".yaml", ".yml"}
API_FIELDS = {
    "VAA_API_KEY",
    "VAA_API_BASE_URL",
    "VAA_AGENT_MODEL",
    "VAA_API_MODE",
    "VAA_API_TIMEOUT_SECONDS",
    "VAA_DAILY_AUTO_LIMIT",
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Bundle:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.catalog = json.loads((self.root / "provenance/catalog.json").read_text("utf-8"))
        if self.catalog.get("schema", self.catalog.get("schema_version")) != 1:
            raise ValueError("Unsupported catalog schema")
        self.original = PurePosixPath(self.catalog["original_root"])
        if not self.original.is_absolute():
            raise ValueError("original_root must be absolute")
        self.project = self.root / "work/project"
        self.rows: dict[str, dict[str, Any]] = {}
        self.paths: dict[str, Path] = {}
        candidates: dict[str, set[Path]] = {}
        for row in self.catalog["files"]:
            source = str(row["source"])
            absolute = str(self.original / source)
            target = self.inside(row["destination"])
            if absolute in self.rows and self.rows[absolute] != row:
                raise ValueError(f"Ambiguous source: {source}")
            self.rows[absolute], self.paths[absolute] = row, target
            old_parent, new_parent = PurePosixPath(absolute).parent, target.parent
            while old_parent != self.original and old_parent != old_parent.parent:
                candidates.setdefault(str(old_parent), set()).add(new_parent)
                old_parent, new_parent = old_parent.parent, new_parent.parent
        self.directories = {
            key: next(iter(values)) for key, values in candidates.items() if len(values) == 1
        }
        self.recipes = {row["id"]: row for row in self.catalog.get("recipes", [])}

    def inside(self, relative: str | Path) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"Path escapes bundle: {relative}")
        return path

    def old_path(self, value: str | Path) -> str:
        path = PurePosixPath(str(value).replace("\\", "/"))
        return str(path if path.is_absolute() else self.original / path)

    def resolve(self, value: str | Path, *, metadata: bool = False) -> Path:
        old = self.old_path(value)
        target = self.paths.get(old) or self.directories.get(old)
        if target is None:
            raise FileNotFoundError(f"No bundled mapping for {value}")
        if metadata and target.suffix.lower() in METADATA_SUFFIXES:
            return self.root / "work/metadata" / target.relative_to(self.root)
        return target

    def verify_payload(self) -> None:
        for row in self.catalog["files"]:
            path = self.inside(row["destination"])
            if not path.is_file():
                raise FileNotFoundError(f"Missing bundled file: {row['destination']}")
            if path.stat().st_size != row["size"]:
                raise ValueError(f"Size mismatch: {row['destination']}")
            with path.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if checksum != row["sha256"]:
                raise ValueError(f"SHA256 mismatch: {row['destination']}")

    def _rewrite_string(self, value: str, source: str) -> str:
        # Resolve relative metadata references before moving their enclosing file.
        old = (
            value
            if PurePosixPath(value).is_absolute()
            else str(PurePosixPath(source).parent / value)
        )
        if old in self.paths or old in self.directories:
            return str(self.resolve(old))
        if str(self.original) in value:

            def relocate_path(token: str) -> str:
                path = PurePosixPath(token)
                for ancestor in (path, *path.parents):
                    target = self.paths.get(str(ancestor)) or self.directories.get(str(ancestor))
                    if target is not None:
                        return str(target / path.relative_to(ancestor))
                return token.replace(str(self.original), str(self.project), 1)

            if value.startswith(str(self.original)) and "\n" not in value:
                return relocate_path(value)
            pattern = re.escape(str(self.original)) + r"[^\s\"',:;)\]]*"
            value = re.sub(pattern, lambda match: relocate_path(match[0]), value)
        return value

    def _rewrite_json(self, value: Any, source: str, field: str = "") -> Any:
        if isinstance(value, dict):
            return {key: self._rewrite_json(item, source, key) for key, item in value.items()}
        if isinstance(value, list):
            return [self._rewrite_json(item, source, field) for item in value]
        if isinstance(value, str):
            path_fields = {"root", "path", "data", "model", "project", "output", "weights"}
            if not PurePosixPath(value).is_absolute() and "/" not in value and "\\" not in value:
                if field not in path_fields and not Path(value).suffix:
                    return value
            return self._rewrite_string(value, source)
        return value

    def prepare(self) -> dict[str, Any]:
        self.verify_payload()
        self.project.mkdir(parents=True, exist_ok=True)
        source = self.root / "source"
        if not source.is_dir():
            raise FileNotFoundError("Missing source snapshot")
        # Never copy local configuration, runtime data, or an existing environment.
        shutil.copytree(
            source,
            self.project,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                ".env", ".env.*", ".git", ".venv", ".export-venv", "__pycache__"
            ),
        )
        (self.project / ".env").write_text("", encoding="utf-8")
        generated = 0
        skipped_large = []
        for old, original in self.paths.items():
            if original.suffix.lower() not in METADATA_SUFFIXES:
                continue
            if original.stat().st_size > 20 * 1024 * 1024:
                # Large source COCO annotations are immutable provenance, not
                # executable training manifests. Keep their hashed originals.
                skipped_large.append(str(original.relative_to(self.root)))
                generated_copy = self.resolve(old, metadata=True)
                if generated_copy.is_file():
                    generated_copy.unlink()
                continue
            target = self.resolve(old, metadata=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            text = original.read_text("utf-8")
            if original.suffix.lower() == ".json":
                write_json(target, self._rewrite_json(json.loads(text), old))
            else:
                # All frozen YOLO datasets use scalar local split paths. Point
                # relative splits at the original bundled image/label directory.
                text = self._rewrite_string(text, old)
                dataset_root = self.directories.get(str(PurePosixPath(old).parent))
                if dataset_root and re.search(r"(?m)^path\s*:", text):
                    text = re.sub(
                        r"(?m)^path\s*:.*$",
                        lambda _, root=dataset_root: "path: " + json.dumps(str(root)),
                        text,
                    )
                elif dataset_root:
                    text = "path: " + json.dumps(str(dataset_root)) + "\n" + text
                target.write_text(text, encoding="utf-8")
            generated += 1
        self._patch_models()
        result = {
            "status": "prepared",
            "project": str(self.project),
            "metadata_files": generated,
            "working_source_adaptations": [
                "bundle model preset paths",
                "disable implicit behavior fallback",
                "YOLO label lookup uses dataset images component",
            ],
            "unconverted_large_provenance": skipped_large,
            "camera_started": False,
            "trained": False,
        }
        write_json(self.root / "work/prepared.json", result)
        return result

    def _patch_models(self) -> None:
        model_file = self.project / "visual_ai_agent/object_models.py"
        if model_file.is_file():
            # Use the original preset identities and checksums, only replacing
            # path arguments. This preserves the UI's actual-model identification.
            text = model_file.read_text("utf-8")
            tree = ast.parse(text)
            lines = text.splitlines(keepends=True)
            edits = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id != "ObjectModelPreset":
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "path" or not isinstance(keyword.value, ast.Call):
                        continue
                    argument = keyword.value.args[0]
                    value = ast.literal_eval(argument)
                    try:
                        target = self.resolve(value)
                    except FileNotFoundError:
                        # A preset absent from this bundle remains visibly unavailable.
                        target = self.project / "unavailable-models" / Path(value).name
                    start = sum(len(line) for line in lines[: argument.lineno - 1])
                    start += argument.col_offset
                    end = sum(len(line) for line in lines[: argument.end_lineno - 1])
                    end += argument.end_col_offset
                    edits.append((start, end, repr(str(target))))
            for start, end, replacement in sorted(edits, reverse=True):
                text = text[:start] + replacement + text[end:]
            model_file.write_text(text, encoding="utf-8")
        controls = self.project / "visual_ai_agent/ui_controls.py"
        if controls.is_file():
            text = controls.read_text("utf-8")
            text = text.replace(
                'Path("harness/artifacts/behavior-20260910-r02")',
                "Path(" + repr(str(self.root / "work/no-implicit-behavior")) + ")",
            )
            controls.write_text(text, encoding="utf-8")
        scene_trainer = self.project / "scripts/train_scene_model.py"
        if scene_trainer.is_file():
            # The frozen runner historically replaced the first absolute path
            # component named images. Choose the dataset's final images component
            # so a user-selected ancestor also named images remains untouched.
            text = scene_trainer.read_text("utf-8").replace(
                'parts.index("images")', 'len(parts) - 1 - parts[::-1].index("images")'
            )
            scene_trainer.write_text(text, encoding="utf-8")

    def ensure_prepared(self) -> None:
        marker = self.root / "work/prepared.json"
        if not marker.is_file() or not (self.project / "uv.lock").is_file():
            raise ValueError("Run prepare first")
        if json.loads(marker.read_text("utf-8")).get("project") != str(self.project):
            raise ValueError("Bundle moved since prepare; run prepare again")
        if (self.project / ".env").read_text("utf-8"):
            raise ValueError("work/project/.env must remain empty; use bundle/.env and --allow-api")

    def clean_environment(self, environ: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ if environ is None else environ)
        provider_prefixes = (
            "VAA_",
            "OPENAI_",
            "ANTHROPIC_",
            "DASHSCOPE_",
            "AZURE_OPENAI_",
            "UV_",
            "PYTHON",
            "VIRTUAL_ENV",
            "CONDA",
            "WANDB_",
            "COMET_",
            "MLFLOW_",
        )
        env = {key: value for key, value in env.items() if not key.startswith(provider_prefixes)}
        env.update(
            {
                "VAA_API_KEY": "",
                "VAA_API_BASE_URL": "",
                "VAA_AGENT_MODEL": "",
                "VAA_DAILY_AUTO_LIMIT": "0",
                "PYTHON_DOTENV_DISABLED": "1",
                "UV_PROJECT_ENVIRONMENT": str(self.project / ".venv"),
                "UV_CACHE_DIR": str(self.root / "work/uv-cache"),
                "UV_PYTHON_INSTALL_DIR": str(self.root / "work/python"),
                "UV_PYTHON_PREFERENCE": "only-managed",
                "PYTHONNOUSERSITE": "1",
            }
        )
        return env

    def launch_plan(
        self, profile: str, *, environ: dict[str, str] | None = None, allow_api: bool = False
    ) -> dict[str, Any]:
        if not (self.root / "work/prepared.json").is_file():
            self.prepare()
        self.ensure_prepared()
        definition = self.catalog.get("profiles", {}).get(profile)
        if not definition:
            raise ValueError(f"Missing profile: {profile}")
        model = self.resolve(definition["object_model"])
        if not model.is_file():
            raise FileNotFoundError(model)
        env = self.clean_environment(environ)
        env.update(
            {
                "VAA_DATA_DIR": str(self.root / "work/runtime" / profile),
                "VAA_MODEL_PATH": str(model),
                "VAA_MODEL_SHA256": self.rows[self.old_path(definition["object_model"])]["sha256"],
                "VAA_BEHAVIOR_ENABLED": str(profile != "official").lower(),
                "VAA_LAPTOP_ENABLED": "false",
                "VAA_SILVER_OBJECT_IGNORE": "false",
                "VAA_CUP_SCALE_RECHECK": str(profile != "official").lower(),
                "VAA_CAMERA_WIDTH": "1280" if profile != "official" else "640",
                "VAA_CAMERA_HEIGHT": "720" if profile != "official" else "480",
                "VAA_BEHAVIOR_MODEL_VERSION": "bundle-" + profile + "-experimental",
            }
        )
        if profile != "official":
            for field, name in (
                ("posture_manifest", "VAA_BEHAVIOR_POSTURE_MANIFEST"),
                ("drinking_manifest", "VAA_BEHAVIOR_DRINKING_MANIFEST"),
            ):
                manifest = self.resolve(definition[field], metadata=True)
                record = json.loads(manifest.read_text("utf-8"))
                onnx = self.inside(record["onnx"])
                if not onnx.is_file():
                    raise FileNotFoundError(onnx)
                env[name] = str(manifest)
        if allow_api:
            config = self.root / ".env"
            if not config.is_file():
                raise FileNotFoundError("--allow-api requires your own bundle/.env")
            for line in config.read_text("utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, separator, value = line.partition("=")
                key = key.strip().removeprefix("export ")
                if not separator or key not in API_FIELDS:
                    raise ValueError(f"Unsupported bundle/.env field: {key}")
                env[key] = value.strip().strip('"').strip("'")
        return {
            "argv": [
                "uv",
                "run",
                "--locked",
                "--no-env-file",
                "streamlit",
                "run",
                "app.py",
                "--server.address",
                "127.0.0.1",
                "--server.port",
                "8511",
            ],
            "cwd": str(self.project),
            "env": env,
            "experimental": profile != "official",
            "camera_started": False,
        }

    def _recipe_dependencies(self, recipe: dict[str, Any], seen: set[str] | None = None) -> None:
        seen = set() if seen is None else seen
        if recipe["id"] in seen:
            raise ValueError("Recipe dependency cycle")
        seen = seen | {recipe["id"]}
        for dependency in recipe.get("dependencies", []):
            if dependency not in self.recipes:
                raise ValueError(f"Unknown recipe dependency: {dependency}")
            upstream = self.recipes[dependency]
            self._recipe_dependencies(upstream, seen)
            for field in ("reference_weights", "reference_onnx"):
                if upstream.get(field) and not self.resolve(upstream[field]).is_file():
                    raise FileNotFoundError(f"Missing dependency {dependency}: {field}")

    def _check_dataset(self, data: Path, train_only: bool) -> dict[str, Any]:
        if data.suffix in {".yaml", ".yml"}:
            content = data.read_text("utf-8")
            fields = dict(re.findall(r"(?m)^(path|train|val|test)\s*:\s*(.*?)\s*$", content))

            def scalar(value: str) -> str:
                value = value.strip()
                if value.startswith('"'):
                    return json.loads(value)
                return value.strip("'").replace("''", "'")

            base_value = scalar(fields.get("path", str(data.parent)))
            base = self.inside(base_value)
            split_roots = {}
            for split in ("train", "val"):
                if split not in fields:
                    raise ValueError(f"Missing dataset split: {split}")
                split_roots[split] = self.inside(base / scalar(fields[split]))
        else:
            split_roots = {
                split: data / split for split in (("train",) if train_only else ("train", "val"))
            }
        counts = {}
        for split, directory in split_roots.items():
            if not directory.is_dir():
                raise FileNotFoundError(f"Missing dataset split: {directory}")
            images = [
                path
                for path in directory.rglob("*")
                if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
            ]
            if not images:
                raise ValueError(f"Empty dataset split: {directory}")
            counts[split] = len(images)
            if data.is_dir():
                classes = [path for path in directory.iterdir() if path.is_dir()]
                if len(classes) < 2 or any(
                    not any(image.is_relative_to(path) for image in images) for path in classes
                ):
                    raise ValueError(f"Missing or empty classification classes: {directory}")
            else:
                for image in images:
                    relative = image.relative_to(base)
                    if relative.parts[0] != "images":
                        raise ValueError(f"Cannot locate YOLO labels for {image}")
                    label = (base / "labels" / Path(*relative.parts[1:])).with_suffix(".txt")
                    if not label.is_file():
                        raise FileNotFoundError(f"Missing YOLO label: {label}")
        return {"images": counts, "format": "classification" if data.is_dir() else "yolo"}

    def _validate_arguments(self, script: Path, arguments: list[str]) -> None:
        """Inspect the bundled CLI declaration without importing its model libraries."""
        tree = ast.parse(script.read_text("utf-8"))
        declaration = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "parse_args"
            ),
            None,
        )
        if declaration is None:
            # The laptop shim delegates to the behavior trainer.
            if script.name == "train_laptop_model.py":
                self._validate_arguments(script.with_name("train_behavior_model.py"), arguments)
            return
        parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
        types = {"int": int, "float": float, "str": str, "Path": Path}
        for node in ast.walk(declaration):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "add_argument":
                continue
            flags = [ast.literal_eval(item) for item in node.args]
            options = {}
            for item in node.keywords:
                if item.arg in {"help", "metavar", "default"}:
                    continue
                if item.arg == "type":
                    options[item.arg] = types[item.value.id]
                elif item.arg == "action" and isinstance(item.value, ast.Attribute):
                    options[item.arg] = argparse.BooleanOptionalAction
                else:
                    options[item.arg] = ast.literal_eval(item.value)
            parser.add_argument(*flags, **options)
        try:
            parser.parse_args(arguments)
        except (argparse.ArgumentError, SystemExit) as exc:
            raise ValueError(f"Recipe parameters do not match bundled trainer: {exc}") from exc

    def train(
        self, recipe_id: str, *, check_only: bool = True, device: str | None = None
    ) -> dict[str, Any]:
        self.ensure_prepared()
        if recipe_id not in self.recipes:
            raise ValueError(f"Unknown recipe: {recipe_id}")
        recipe = self.recipes[recipe_id]
        if recipe.get("validity", "ready") != "ready":
            raise ValueError(f"Recipe {recipe_id} is {recipe.get('validity')}; retained as history")
        self._recipe_dependencies(recipe)
        dataset = next(
            (row for row in self.catalog["datasets"] if row["id"] == recipe["dataset_id"]), None
        )
        if dataset is None:
            raise ValueError("Recipe has unknown dataset_id")
        for field in ("source_weights", "reference_weights", "reference_onnx", "record"):
            if recipe.get(field) and not self.resolve(recipe[field]).is_file():
                raise FileNotFoundError(f"Missing recipe {field}: {recipe[field]}")
        if dataset.get("manifest") and not self.resolve(dataset["manifest"]).is_file():
            raise FileNotFoundError("Missing dataset manifest")
        script = self.project / recipe["script"]
        if not script.is_file() or not script.resolve().is_relative_to(self.project):
            raise FileNotFoundError(f"Missing bundled trainer: {script}")
        arguments = [str(item) for item in recipe["argv"]]
        # catalog argv contains arguments only, preserving the original CLI flags.
        path_flags = {"--data", "--weights", "--selection-manifest"}
        output = self.root / "work/training" / recipe_id
        if output.exists():
            raise FileExistsError(f"Refusing to overwrite training output: {output}")
        for index, argument in enumerate(arguments):
            if argument in path_flags:
                if index + 1 == len(arguments):
                    raise ValueError(f"Missing value for {argument}")
                arguments[index + 1] = str(
                    self.resolve(arguments[index + 1], metadata=(argument != "--weights"))
                )
            elif argument == "--output":
                arguments[index + 1] = str(output)
        if "--output" not in arguments:
            arguments += ["--output", str(output)]
        if device is not None:
            if "--device" in arguments:
                arguments[arguments.index("--device") + 1] = device
            else:
                arguments += ["--device", device]
        for field in ("--epochs", "--imgsz", "--batch", "--threads"):
            if field in arguments and int(arguments[arguments.index(field) + 1]) <= 0:
                raise ValueError(f"Invalid parameter: {field}")
        if "--data" not in arguments or "--weights" not in arguments:
            raise ValueError("Recipe requires --data and --weights")
        weights = Path(arguments[arguments.index("--weights") + 1])
        if weights != self.resolve(recipe["source_weights"]):
            raise ValueError("Recipe --weights differs from recorded source_weights")
        data = Path(arguments[arguments.index("--data") + 1])
        dataset_root = self.resolve(dataset["source_dir"])
        actual_data = self.resolve(recipe["argv"][recipe["argv"].index("--data") + 1])
        if not actual_data.is_relative_to(dataset_root):
            raise ValueError("Recipe --data differs from recorded dataset")
        self._validate_arguments(script, arguments)
        lock = tomllib.loads((self.project / "uv.lock").read_text("utf-8"))
        locked = {row["name"]: row["version"] for row in lock.get("package", [])}
        for name in ("torch", "ultralytics", "onnx", "onnxslim"):
            if name not in locked:
                raise ValueError(f"Training dependency missing from uv.lock: {name}")
        recorded = recipe.get("versions", {})
        version_differences = {
            key: {"recorded": value, "locked": locked[key]}
            for key, value in recorded.items()
            if key in locked and str(value) != locked[key]
        }
        summary = self._check_dataset(data, "--train-only" in arguments)
        for argument in arguments:
            if str(self.original) in argument and not Path(argument).is_relative_to(self.root):
                raise ValueError(f"Unrelocated recipe argument: {argument}")
        command = [
            "uv",
            "run",
            "--locked",
            "--group",
            "export",
            "--no-env-file",
            "python",
            str(script),
            *arguments,
        ]
        result = {
            "status": "not_trained",
            "recipe": recipe_id,
            "argv": command,
            "dataset": summary,
            "output": str(output),
            "dependencies": recipe.get("dependencies", []),
            "versions": recorded,
            "locked_versions": locked,
            "version_differences": version_differences,
            "dependency_installation": "uv.lock; installed only when executing training",
            "validation": "paths, payload presence, data layout, parameters; no model import",
        }
        if check_only:
            return result
        # Ultralytics creates image/label caches next to its inputs. Give an
        # explicit training run a private writable dataset; frozen delivery
        # images and labels remain untouched. Preflight never performs this copy.
        training_data = self.root / "work/training-data" / recipe_id
        if training_data.exists():
            raise FileExistsError(f"Refusing to overwrite training data: {training_data}")
        shutil.copytree(dataset_root, training_data)
        for old_path, payload in self.paths.items():
            if not payload.is_relative_to(dataset_root) or payload.suffix not in METADATA_SUFFIXES:
                continue
            working = self.resolve(old_path, metadata=True)
            destination = training_data / payload.relative_to(dataset_root)
            destination.write_text(
                working.read_text("utf-8").replace(str(dataset_root), str(training_data)),
                encoding="utf-8",
            )
        arguments[arguments.index("--data") + 1] = str(
            training_data / actual_data.relative_to(dataset_root)
        )
        if "--selection-manifest" in arguments:
            selection_index = arguments.index("--selection-manifest") + 1
            selection = Path(arguments[selection_index])
            selection_copy = training_data / "bundle-selection.json"
            selection_copy.write_text(
                selection.read_text("utf-8").replace(str(dataset_root), str(training_data)),
                encoding="utf-8",
            )
            arguments[selection_index] = str(selection_copy)
        command = [*command[:8], *arguments]
        result["argv"] = command
        result["training_data"] = str(training_data)
        env = self.clean_environment()
        env.update(
            {
                "UV_PROJECT_ENVIRONMENT": str(self.project / ".export-venv"),
                "YOLO_CONFIG_DIR": str(self.root / "work/yolo-settings"),
                "YOLO_AUTOINSTALL": "false",
                "YOLO_OFFLINE": "true",
                "ULTRALYTICS_SKIP_REQUIREMENTS_CHECKS": "true",
                "WANDB_MODE": "disabled",
            }
        )
        completed = subprocess.run(command, cwd=self.project, env=env, check=False)
        return {
            **result,
            "status": "completed" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare", help="verify files and prepare a relocatable working copy")
    launch = sub.add_parser("launch", help="open local UI; observation still requires a user click")
    launch.add_argument(
        "--profile", choices=("official", "desktop-reference", "candidates"), default="official"
    )
    launch.add_argument(
        "--allow-api",
        action="store_true",
        help="read only your own bundle/.env; inherited API variables are removed",
    )
    training = sub.add_parser(
        "train", help="explicit training; use --check-only for offline preflight"
    )
    training.add_argument("--recipe", required=True)
    training.add_argument("--check-only", action="store_true")
    training.add_argument("--device", help="explicit override of recorded cpu/mps/CUDA device")
    sub.add_parser("diff", help="show documented source/reference/candidate differences")
    verify = sub.add_parser("verify", help="forward to the standalone bundle verifier")
    replay = sub.add_parser("replay", help="run verifier's explicit replay mode")
    for verification in (verify, replay):
        verification.add_argument("--output", type=Path)
        verification.add_argument("--reference-root", type=Path)
    args = parser.parse_args(argv)
    try:
        bundle = Bundle(args.root)
        if args.command == "prepare":
            result = bundle.prepare()
        elif args.command == "launch":
            plan = bundle.launch_plan(args.profile, allow_api=args.allow_api)
            print(
                f"Profile {args.profile}; experimental={plan['experimental']}; "
                "camera remains stopped until Start observation",
                flush=True,
            )
            return subprocess.run(
                plan["argv"], cwd=plan["cwd"], env=plan["env"], check=False
            ).returncode
        elif args.command == "train":
            result = bundle.train(args.recipe, check_only=args.check_only, device=args.device)
        elif args.command in {"verify", "replay"}:
            command = [
                sys.executable,
                str(bundle.root / "tools/verify.py"),
                "--bundle",
                str(bundle.root),
            ]
            if args.command == "replay":
                command.append("--replay")
            if args.output:
                command += ["--output", str(args.output)]
            if args.reference_root:
                command += ["--reference-root", str(args.reference_root)]
            return subprocess.run(command, cwd=bundle.root, check=False).returncode
        else:
            print((bundle.root / "reports/differences.md").read_text("utf-8"))
            return 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(result.get("returncode", 0))
    except (OSError, ValueError, KeyError) as exc:
        print(f"Bundle error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
