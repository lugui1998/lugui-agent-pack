#!/usr/bin/env python3
"""Conservative, cross-platform installer for the Lugui Codex Agent Kit.

Planning performs all parse, ownership, and conflict checks without modifying
the target. Commit starts only when planning has no blockers and records every
operation that completed, even when a later operation fails.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


STATE_HEADER = "# lugui-agent-pack state v2"


@dataclass(frozen=True)
class StateRecord:
    kind: str
    path: str
    hash: str
    mode: str
    source: str
    created: str = "false"


@dataclass
class Operation:
    label: str
    path: Path | str
    note: str
    perform: Callable[[], Iterable[StateRecord]]


class Installer:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.conflicts = 0
        self.choices = 0
        self.notices: set[tuple[str, str]] = set()
        self.operations: list[Operation] = []
        self.pending_state: list[StateRecord] = []
        self.state: list[StateRecord] = []
        self.root = Path(__file__).resolve().parent.parent
        self.manifest = json.loads((self.root / "kit.json").read_text(encoding="utf-8"))
        if self.manifest.get("name") != "lugui-agent-pack":
            raise ValueError("unexpected kit.json name")
        self.instructions_source = self.manifest_source("instructions", "source")
        self.agents_source = self.manifest_source("agents", "source")
        self.agent_catalog_source = (
            self.manifest_source("agentCatalog")
            if "agentCatalog" in self.manifest
            else None
        )
        self.config_source = self.manifest_source("projectConfig")
        self.skill_source = self.manifest_source("skill", "source")
        self.skill_name = self.manifest_leaf("skill", "name")
        marker = self.manifest_leaf("instructions", "marker")
        self.begin = f"<!-- BEGIN {marker} -->"
        self.end = f"<!-- END {marker} -->"

        codex_home = os.environ.get("CODEX_HOME")
        self.home = Path(codex_home or Path.home() / ".codex").expanduser().resolve()
        self.project = Path(args.target_project or Path.cwd()).expanduser().resolve()
        if args.scope == "project" and self.project == self.root:
            raise ValueError("target project cannot be the kit checkout itself")

        self.target = self.home if args.scope == "global" else self.project
        state_key = "global" if args.scope == "global" else "project-" + self.sha_text(str(self.project))[:16]
        self.state_path = self.home / "lugui-agent-pack" / "state" / f"{state_key}.tsv"
        self.run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.state_path_safe = self.path_within(self.state_path, self.home)
        if self.state_path_safe:
            self.load_state()

    def manifest_value(self, *keys: str) -> str:
        value: object = self.manifest
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                raise ValueError(f"kit.json is missing {'.'.join(keys)}")
            value = value[key]
        if not isinstance(value, str) or not value:
            raise ValueError(f"kit.json {'.'.join(keys)} must be a non-empty string")
        return value

    def manifest_source(self, *keys: str) -> Path:
        field = ".".join(keys)
        value = self.manifest_value(*keys)
        relative = Path(value)
        segments = value.replace("\\", "/").split("/")
        if relative.is_absolute() or relative.drive or ".." in segments:
            raise ValueError(f"kit.json {field} must be a relative descendant of the kit root")
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise ValueError(f"kit.json {field} resolves outside the kit root") from error
        if candidate == self.root:
            raise ValueError(f"kit.json {field} must identify a descendant of the kit root")
        return candidate

    def manifest_leaf(self, *keys: str) -> str:
        field = ".".join(keys)
        value = self.manifest_value(*keys)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
            raise ValueError(f"kit.json {field} must be a safe leaf name")
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if value.split(".", 1)[0].upper() in reserved:
            raise ValueError(f"kit.json {field} uses a reserved leaf name")
        return value

    @staticmethod
    def path_within(path: Path, root: Path, allow_leaf_link: bool = False) -> bool:
        checked = path.parent if allow_leaf_link else path
        try:
            checked.resolve().relative_to(root)
            return True
        except (OSError, ValueError):
            return False

    def require_target(
        self,
        path: Path,
        root: Path | None = None,
        allow_leaf_link: bool = False,
    ) -> bool:
        intended_root = root or self.target
        if self.path_within(path, intended_root, allow_leaf_link):
            return True
        self.conflict(path, f"resolved target escapes intended root {intended_root}")
        return False

    def assert_target(
        self,
        path: Path,
        root: Path | None = None,
        allow_leaf_link: bool = False,
    ) -> None:
        intended_root = root or self.target
        if not self.path_within(path, intended_root, allow_leaf_link):
            raise OSError(f"resolved target escapes intended root {intended_root}")

    @staticmethod
    def sha_text(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def sha_bytes(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def file_hash(path: Path) -> str:
        return Installer.sha_bytes(path.read_bytes())

    @staticmethod
    def value_hash(value: object) -> str:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return Installer.sha_text(canonical)

    @staticmethod
    def toml_value(value: object) -> str:
        if isinstance(value, str):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, (int, float)):
            return str(value)
        raise ValueError(f"unsupported managed TOML value: {value!r}")

    def source_id(self, source: Path) -> str:
        try:
            return source.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(source.resolve())

    def action(self, label: str, path: Path | str, note: str) -> None:
        print(f"[{label}] {path} - {note}")

    def conflict(self, path: Path | str, note: str) -> None:
        self.conflicts += 1
        self.action("CONFLICT", path, note)

    def notice(self, path: Path | str, note: str) -> None:
        key = (str(path), note)
        if key not in self.notices:
            self.notices.add(key)
            self.action("NOTICE", path, note)

    def add_operation(
        self,
        label: str,
        path: Path | str,
        note: str,
        perform: Callable[[], Iterable[StateRecord]],
    ) -> None:
        self.action(label, path, note)
        self.operations.append(Operation(label, path, note, perform))

    def load_state(self) -> None:
        if not self.state_path.is_file():
            return
        for line in self.state_path.read_text(encoding="utf-8").splitlines():
            fields = line.split("\t")
            if len(fields) >= 7 and fields[0] == "managed":
                self.state.append(StateRecord(*fields[1:7]))

    def owned(self, kind: str, path: Path, source: str | None = None) -> StateRecord | None:
        for record in self.state:
            if record.kind != kind or record.path != str(path):
                continue
            if source is None or record.source == source:
                return record
        return None

    def put_state(self, record: StateRecord) -> None:
        if record.kind == "config-key":
            self.state = [
                old for old in self.state
                if not (old.kind == "config" and old.path == record.path)
            ]
        self.state = [
            old
            for old in self.state
            if not (
                old.kind == record.kind
                and old.path == record.path
                and old.source == record.source
            )
        ]
        self.state.append(record)

    def save_state(self) -> None:
        self.assert_target(self.state_path, self.home)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            STATE_HEADER,
            f"kitName\t{self.manifest['name']}",
            f"kitVersion\t{self.manifest['version']}",
            f"scope\t{self.args.scope}",
        ]
        for record in sorted(self.state, key=lambda item: (item.kind, item.path, item.source)):
            lines.append(
                "\t".join(
                    (
                        "managed",
                        record.kind,
                        record.path,
                        record.hash,
                        record.mode,
                        record.source,
                        record.created,
                    )
                )
            )
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        temporary = self.state_path.with_name(f".{self.state_path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, self.state_path)

    def backup_path(self, path: Path) -> Path:
        name = f"{self.sha_text(str(path))[:16]}-{path.name}"
        return self.state_path.parent / "backups" / self.run_stamp / name

    def replace_bytes(self, path: Path, payload: bytes, backup: bool) -> None:
        self.assert_target(path)
        if backup and path.is_file():
            destination = self.backup_path(path)
            self.assert_target(destination, self.home)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, path)

    def write_and_record(
        self,
        path: Path,
        payload: bytes,
        backup: bool,
        records: Iterable[StateRecord],
    ) -> Iterable[StateRecord]:
        self.replace_bytes(path, payload, backup)
        return records

    def plan_backup(self, path: Path) -> None:
        if path.is_file():
            self.action("BACKUP", path, str(self.backup_path(path)))

    def plan_file(self, kind: str, source: Path, target: Path) -> None:
        if not self.require_target(target):
            return
        payload = source.read_bytes()
        wanted = self.sha_bytes(payload)
        source_id = self.source_id(source)
        old = self.owned(kind, target)
        record = StateRecord(kind, str(target), wanted, "file", source_id, str(not target.exists()).lower())

        if not target.exists():
            self.add_operation(
                "ADD",
                target,
                f"create from {source}",
                lambda: self.write_and_record(target, payload, False, [record]),
            )
        elif target.is_file() and self.file_hash(target) == wanted:
            self.action("SKIP", target, "already matches the kit")
            self.pending_state.append(record)
        elif target.is_file() and old and self.file_hash(target) == old.hash:
            self.plan_backup(target)
            self.add_operation(
                "UPDATE",
                target,
                "previously managed and unchanged",
                lambda: self.write_and_record(target, payload, True, [record]),
            )
        else:
            self.conflict(target, "existing content differs; it remains untouched")

    def instruction_block(self, newline: str = "\n") -> str:
        source = self.instructions_source.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip()
        return newline.join((self.begin, f"kit-version: {self.manifest['version']}", source, self.end))

    def plan_instructions(self) -> None:
        target = self.target / "AGENTS.md"
        source = self.instructions_source

        if self.args.instructions == "skip":
            self.action("SKIP", target, "requested by --instructions skip")
            return
        if not self.require_target(target):
            return

        if not target.exists():
            block = self.instruction_block()
            payload = (block + "\n").encode("utf-8")
            record = StateRecord("instructions", str(target), self.sha_text(block), "managed-block", self.source_id(source), "true")
            self.add_operation(
                "ADD",
                target,
                "create managed instruction block",
                lambda: self.write_and_record(target, payload, False, [record]),
            )
            return

        raw = target.read_bytes()
        text = raw.decode("utf-8")
        newline = "\r\n" if b"\r\n" in raw else "\n"
        block = self.instruction_block(newline)
        wanted = self.sha_text(block)
        old = self.owned("instructions", target)
        starts = [match.start() for match in re.finditer(re.escape(self.begin), text)]
        ends = [match.start() for match in re.finditer(re.escape(self.end), text)]

        if len(starts) > 1 or len(ends) > 1 or len(starts) != len(ends) or (starts and starts[0] > ends[0]):
            self.conflict(target, "malformed or duplicate managed instruction markers")
            return

        if starts:
            match = re.search(re.escape(self.begin) + r".*?" + re.escape(self.end), text, re.S)
            assert match is not None
            current = self.sha_text(match.group(0))
            record = StateRecord("instructions", str(target), wanted, "managed-block", self.source_id(source), "false")
            if current == wanted:
                self.action("SKIP", target, "managed instruction block is current")
                self.pending_state.append(record)
            elif old and old.hash == current:
                result = text[: match.start()] + block + text[match.end() :]
                self.plan_backup(target)
                self.add_operation(
                    "UPDATE",
                    target,
                    "replace unchanged managed instruction block",
                    lambda: self.write_and_record(target, result.encode("utf-8"), True, [record]),
                )
            else:
                self.conflict(target, "managed instruction block was modified or is unowned")
            return

        source_text = source.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
        if source_text and source_text in text.replace("\r\n", "\n"):
            self.notice(target, "kit instructions already appear without managed markers; appending would duplicate them")

        if self.args.instructions == "ask":
            self.choices += 1
            self.action("PROMPT", target, "existing AGENTS.md found; choose --instructions append, overwrite, or skip")
            return

        record = StateRecord("instructions", str(target), wanted, "managed-block", self.source_id(source), "false")
        self.plan_backup(target)
        if self.args.instructions == "overwrite":
            result = block + newline
            label, note = "OVERWRITE", "replace entire AGENTS.md after backup"
        else:
            result = text.rstrip() + newline * 2 + block + newline
            label, note = "MERGE", "append managed instruction block"
        self.add_operation(
            label,
            target,
            note,
            lambda: self.write_and_record(target, result.encode("utf-8"), True, [record]),
        )

    @staticmethod
    def parse_toml(path: Path) -> tuple[dict, str]:
        text = path.read_bytes().decode("utf-8")
        return tomllib.loads(text), text

    @staticmethod
    def managed_config(data: dict) -> dict[str, object]:
        managed: dict[str, object] = {}
        for key in ("model", "model_reasoning_effort"):
            if key in data:
                managed[key] = data[key]
        agents = data.get("agents", {})
        if not isinstance(agents, dict):
            raise ValueError("the agents setting must be a table")
        for key, value in agents.items():
            managed[f"agents.{key}"] = value
        return managed

    @staticmethod
    def find_comment(value: str) -> int | None:
        quote: str | None = None
        escaped = False
        for index, character in enumerate(value):
            if escaped:
                escaped = False
            elif character == "\\" and quote == '"':
                escaped = True
            elif quote and character == quote:
                quote = None
            elif not quote and character in ("'", '"'):
                quote = character
            elif not quote and character == "#":
                return index
        return None

    @staticmethod
    def assignment_pattern(key: str) -> re.Pattern[str]:
        return re.compile(r"^(?P<indent>\s*)" + re.escape(key) + r"(?P<gap>\s*=\s*)(?P<value>.*?)(?P<ending>\r?\n)?$")

    def edit_config_value(self, text: str, key: str, value: object) -> tuple[str | None, str | None]:
        """Edit one supported key while preserving unrelated text and comments."""
        lines = text.splitlines(keepends=True)
        newline = "\r\n" if "\r\n" in text else "\n"
        first_table = next((i for i, line in enumerate(lines) if re.match(r"^\s*\[", line)), len(lines))
        rendered = self.toml_value(value)

        if "." not in key:
            pattern = self.assignment_pattern(key)
            for index in range(first_table):
                match = pattern.match(lines[index])
                if match:
                    tail = match.group("value")
                    comment_at = self.find_comment(tail)
                    comment = "" if comment_at is None else tail[comment_at:].lstrip()
                    ending = match.group("ending") or ""
                    suffix = f" {comment}" if comment else ""
                    lines[index] = f"{match.group('indent')}{key}{match.group('gap')}{rendered}{suffix}{ending}"
                    return "".join(lines), None
            insertion = f"{key} = {rendered}{newline}"
            if first_table and lines[first_table - 1].strip():
                insertion += newline
            lines.insert(first_table, insertion)
            return "".join(lines), None

        section, child = key.split(".", 1)
        inline_pattern = self.assignment_pattern(section)
        dotted_pattern = self.assignment_pattern(key)
        for index in range(first_table):
            if inline_pattern.match(lines[index]):
                return None, f"cannot safely edit {key} inside inline table {section}"
            match = dotted_pattern.match(lines[index])
            if match:
                tail = match.group("value")
                comment_at = self.find_comment(tail)
                comment = "" if comment_at is None else tail[comment_at:].lstrip()
                ending = match.group("ending") or ""
                suffix = f" {comment}" if comment else ""
                lines[index] = f"{match.group('indent')}{key}{match.group('gap')}{rendered}{suffix}{ending}"
                return "".join(lines), None

        section_pattern = re.compile(r"^\s*\[" + re.escape(section) + r"\]\s*(?:#.*)?(?:\r?\n)?$")
        section_start = next((i for i, line in enumerate(lines) if section_pattern.match(line)), None)
        if section_start is not None:
            section_end = next((i for i in range(section_start + 1, len(lines)) if re.match(r"^\s*\[", lines[i])), len(lines))
            child_pattern = self.assignment_pattern(child)
            for index in range(section_start + 1, section_end):
                match = child_pattern.match(lines[index])
                if match:
                    tail = match.group("value")
                    comment_at = self.find_comment(tail)
                    comment = "" if comment_at is None else tail[comment_at:].lstrip()
                    ending = match.group("ending") or ""
                    suffix = f" {comment}" if comment else ""
                    lines[index] = f"{match.group('indent')}{child}{match.group('gap')}{rendered}{suffix}{ending}"
                    return "".join(lines), None
            lines.insert(section_end, f"{child} = {rendered}{newline}")
            return "".join(lines), None

        dotted_indexes = [i for i in range(first_table) if re.match(r"^\s*" + re.escape(section) + r"\.", lines[i])]
        if dotted_indexes:
            lines.insert(dotted_indexes[-1] + 1, f"{key} = {rendered}{newline}")
            return "".join(lines), None

        prefix = "" if not text or text.endswith(("\n", "\r")) else newline
        if text and text.strip():
            prefix += newline
        return text + prefix + f"[{section}]{newline}{child} = {rendered}{newline}", None

    def plan_config(self) -> None:
        conflicts_before = self.conflicts
        source = self.config_source
        project_relative = self.config_source.relative_to(self.root)
        target = self.target / "config.toml" if self.args.scope == "global" else self.target / project_relative

        if self.args.scope == "global" and self.args.global_defaults == "preserve":
            self.action("SKIP", target, "global defaults preserved; use --global-defaults merge or replace to opt in")
            return
        if self.args.config != "preserve" and not self.require_target(target):
            return

        try:
            desired_data, _ = self.parse_toml(source)
            desired = self.managed_config(desired_data)
        except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValueError) as error:
            self.conflict(source, f"invalid kit TOML: {error}")
            return

        if target.exists():
            try:
                current_data, text = self.parse_toml(target)
            except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
                self.conflict(target, f"invalid TOML: {error}")
                return
        else:
            current_data, text = {}, ""

        if self.args.config == "preserve":
            self.action("SKIP", target, "existing configuration preserved")
            self.report_effective(current_data)
            return
        try:
            current = self.managed_config(current_data)
        except ValueError as error:
            self.conflict(target, f"unsupported configuration layout: {error}")
            return
        result = text
        changed: list[str] = []
        records: list[StateRecord] = []
        old_whole_file = self.owned("config", target)

        for key, desired_value in desired.items():
            old_key = self.owned("config-key", target, key)
            present = key in current
            current_value = current.get(key)
            should_write = False

            if self.args.config == "replace":
                should_write = not present or current_value != desired_value
            elif old_key:
                if present and self.value_hash(current_value) == old_key.hash:
                    should_write = current_value != desired_value
                elif present and current_value == desired_value:
                    should_write = False
                else:
                    self.conflict(target, f"managed setting {key} was modified by the user")
                    continue
            elif not present:
                should_write = True
            elif current_value == desired_value:
                should_write = False
            else:
                continue  # Existing unowned values belong to the user in merge mode.

            if should_write:
                edited, error = self.edit_config_value(result, key, desired_value)
                if error:
                    self.conflict(target, error)
                    continue
                assert edited is not None
                result = edited
                changed.append(key)

            if should_write or current_value == desired_value or (old_key and present):
                records.append(StateRecord("config-key", str(target), self.value_hash(desired_value), "toml-key", key, str(not target.exists()).lower()))

        if self.conflicts > conflicts_before:
            return

        try:
            projected = tomllib.loads(result)
        except tomllib.TOMLDecodeError as error:
            self.conflict(target, f"projected configuration is invalid TOML: {error}")
            return

        if old_whole_file and records:
            self.notice(target, "upgrading compatible v1 configuration ownership to per-key state")

        if changed:
            backup = target.is_file()
            if backup:
                self.plan_backup(target)
            label = "ADD" if not target.exists() else ("REPLACE" if self.args.config == "replace" else "MERGE")
            self.add_operation(
                label,
                target,
                "managed settings: " + ", ".join(changed),
                lambda: self.write_and_record(target, result.encode("utf-8"), backup, records),
            )
        else:
            self.action("SKIP", target, "user settings preserved; managed values are already current")
            self.pending_state.extend(records)

        self.report_effective(projected)

    @staticmethod
    def report_effective(data: dict) -> None:
        print("Effective file defaults: model=%s effort=%s (session/app overrides can take precedence)" % (data.get("model", "unset"), data.get("model_reasoning_effort", "unset")))

    def plan_overlap_notices(self) -> None:
        if self.args.scope != "project":
            return
        global_instructions = self.home / "AGENTS.md"
        project_instructions = self.project / "AGENTS.md"
        if global_instructions.is_file() and (
            project_instructions.is_file() or self.args.instructions != "skip"
        ):
            self.notice(project_instructions, f"project instructions will layer with global instructions at {global_instructions}")

        global_config = self.home / "config.toml"
        project_config = self.project / ".codex" / "config.toml"
        if global_config.is_file() and project_config.is_file():
            try:
                global_data, _ = self.parse_toml(global_config)
                project_data, _ = self.parse_toml(project_config)
                if global_data.get("model") and project_data.get("model"):
                    if global_data["model"] == project_data["model"]:
                        self.notice(project_config, f"project and global configuration both select {project_data['model']}")
                    else:
                        self.notice(project_config, f"project model {project_data['model']} overrides global model {global_data['model']}")
            except (OSError, UnicodeError, tomllib.TOMLDecodeError):
                self.notice(project_config, "could not compare global and project model settings")

    @staticmethod
    def agent_name(path: Path) -> str | None:
        try:
            data, _ = Installer.parse_toml(path)
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            return None
        name = data.get("name")
        return name if isinstance(name, str) else None

    def plan_agent_notices(self, target_agents: Path, source_agents: list[Path]) -> None:
        desired_names: dict[str, Path] = {}
        source_names = {source.name for source in source_agents}
        for source in source_agents:
            name = self.agent_name(source)
            if not name:
                self.conflict(source, "agent file has no valid name")
            elif name in desired_names:
                self.conflict(source, f"kit duplicates agent name {name!r} from {desired_names[name]}")
            else:
                desired_names[name] = source

        if target_agents.is_dir():
            for existing in target_agents.glob("*.toml"):
                if existing.name in source_names:
                    continue
                name = self.agent_name(existing)
                if name in desired_names:
                    self.notice(existing, f"agent name {name!r} duplicates kit role file {desired_names[name].name}")

        if self.args.scope == "project":
            global_agents = self.home / "agents"
            if global_agents.is_dir():
                global_by_name = {name: path for path in global_agents.glob("*.toml") if (name := self.agent_name(path)) is not None}
                for name, source in desired_names.items():
                    if name in global_by_name:
                        self.notice(target_agents / source.name, f"project role {name!r} also exists globally at {global_by_name[name]}")

    def preflight_agent_catalog(self, source_agents: list[Path]) -> None:
        """Check an advertised role catalog against role files before planning writes."""
        catalog = self.agent_catalog_source
        if catalog is None:
            return  # Older manifests did not advertise a catalog.
        try:
            data = json.loads(catalog.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            self.conflict(catalog, f"invalid agent catalog: {error}")
            return
        if not isinstance(data, dict) or data.get("schemaVersion") != 1:
            self.conflict(catalog, "unsupported agent catalog schema; expected schemaVersion 1")
            return
        if not isinstance(data, dict) or not isinstance(data.get("roles"), list):
            self.conflict(catalog, "agent catalog must contain a roles list")
            return

        source_by_name: dict[str, tuple[Path, dict]] = {}
        for source in source_agents:
            try:
                role, _ = self.parse_toml(source)
            except (OSError, UnicodeError, tomllib.TOMLDecodeError):
                continue
            name = role.get("name")
            if isinstance(name, str) and name and name not in source_by_name:
                source_by_name[name] = (source, role)

        catalog_names: set[str] = set()
        valid = True
        for index, entry in enumerate(data["roles"]):
            if not isinstance(entry, dict):
                self.conflict(catalog, f"catalog role {index} must be an object")
                valid = False
                continue
            name, model, effort = (entry.get(key) for key in ("name", "model", "effort"))
            if not all(isinstance(value, str) and value for value in (name, model, effort)):
                self.conflict(catalog, f"catalog role {index} requires non-empty name, model, and effort")
                valid = False
                continue
            if name in catalog_names:
                self.conflict(catalog, f"catalog duplicates role name {name!r}")
                valid = False
                continue
            catalog_names.add(name)
            source_entry = source_by_name.get(name)
            if source_entry is None:
                self.conflict(catalog, f"catalog role {name!r} has no corresponding source role definition")
                valid = False
                continue
            source, role = source_entry
            if role.get("model") != model or role.get("model_reasoning_effort") != effort:
                self.conflict(source, f"role {name!r} model/effort does not match catalog ({model}, {effort})")
                valid = False

        if valid:
            self.notice(
                catalog,
                "static catalog checked; account/runtime model availability is UNKNOWN offline; use runtime verification and documented fallbacks",
            )

    @staticmethod
    def same_location(first: Path, second: Path) -> bool:
        try:
            return os.path.samefile(first, second)
        except OSError:
            try:
                return first.resolve(strict=True) == second.resolve(strict=True)
            except OSError:
                return False

    def create_directory_link(self, target: Path, source: Path) -> str:
        self.assert_target(target, allow_leaf_link=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.symlink_to(source, target_is_directory=True)
            return "symlink"
        except OSError as symlink_error:
            if os.name != "nt":
                raise
            result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(target), str(source)], text=True, capture_output=True)
            if result.returncode != 0:
                raise OSError(f"symlink failed ({symlink_error}); junction failed ({result.stderr.strip()})")
            return "junction"

    @staticmethod
    def normalize_git_url(value: str) -> str:
        value = value.strip().rstrip("/")
        if value.endswith(".git"):
            value = value[:-4]
        if "://" not in value and not value.startswith("git@"):
            try:
                return os.path.normcase(str(Path(value).expanduser().resolve()))
            except OSError:
                pass
        return value.casefold()

    @staticmethod
    def git(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(project), *arguments], text=True, capture_output=True)

    @staticmethod
    def submodule_url(project: Path, relative: str) -> str | None:
        modules = project / ".gitmodules"
        if not modules.is_file():
            return None
        parser = configparser.RawConfigParser()
        try:
            parser.read(modules, encoding="utf-8")
        except configparser.Error:
            return None
        normalized = relative.replace("\\", "/")
        for section in parser.sections():
            if section.startswith("submodule ") and parser.get(section, "path", fallback="").replace("\\", "/") == normalized:
                return parser.get(section, "url", fallback=None)
        return None

    def verified_submodule(self, target: Path, upstream: str) -> bool:
        if not target.is_dir() or not (target / ".git").exists():
            return False
        remote = self.git(target, "remote", "get-url", "origin")
        return remote.returncode == 0 and self.normalize_git_url(remote.stdout) == self.normalize_git_url(upstream)

    def plan_skills(self) -> None:
        if self.args.skills == "skip":
            self.action("SKIP", "skills", "requested by --skills skip")
            return

        source = self.skill_source
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            self.conflict(source, "kit skill source is unavailable; initialize submodules first")
            return

        if self.args.scope == "global":
            target = self.home / "skills" / self.skill_name
            if not self.require_target(target, allow_leaf_link=True):
                return
            source_id = self.source_id(source)
            if target.exists() or target.is_symlink():
                if self.same_location(target, source):
                    mode = "symlink" if target.is_symlink() else "junction"
                    self.action("SKIP", target, f"existing {mode} already points to the kit skill")
                    self.pending_state.append(StateRecord("skill-link", str(target), self.file_hash(source / "SKILL.md"), mode, source_id, "false"))
                else:
                    self.conflict(target, "existing personal skill path points elsewhere")
                return

            def link() -> Iterable[StateRecord]:
                mode = self.create_directory_link(target, source)
                return [StateRecord("skill-link", str(target), self.file_hash(source / "SKILL.md"), mode, source_id, "true")]

            self.add_operation("LINK", target, f"link to {source} (symlink, with Windows junction fallback)", link)
            return

        relative = self.skill_source.relative_to(self.root).as_posix()
        target = self.project / relative
        upstream = self.manifest["skill"]["upstream"]
        if not self.require_target(target):
            return
        if not self.require_target(self.project / ".gitmodules"):
            return
        if self.git(self.project, "rev-parse", "--show-toplevel").returncode != 0:
            self.conflict(target, "project skill installation requires a Git repository")
            return

        configured = self.submodule_url(self.project, relative)
        if configured:
            if self.normalize_git_url(configured) != self.normalize_git_url(upstream):
                self.conflict(target, f".gitmodules configures a different upstream: {configured}")
                return
            if self.verified_submodule(target, upstream):
                self.action("SKIP", target, "existing Git submodule is configured for the expected upstream")
                self.pending_state.append(StateRecord("skill-submodule", str(target), self.file_hash(target / "SKILL.md"), "submodule", upstream, "false"))
                return

            target_empty = not target.exists() or (target.is_dir() and not any(target.iterdir()))
            if not target_empty:
                self.conflict(target, "configured submodule is modified, partially initialized, or cannot be verified")
                return

            def initialize() -> Iterable[StateRecord]:
                self.assert_target(target)
                self.assert_target(self.project / ".gitmodules")
                result = self.git(self.project, "submodule", "update", "--init", "--", relative)
                if result.returncode != 0 or not self.verified_submodule(target, upstream):
                    raise RuntimeError(result.stderr.strip() or "submodule initialization could not be verified")
                return [StateRecord("skill-submodule", str(target), self.file_hash(target / "SKILL.md"), "submodule", upstream, "true")]

            self.add_operation("SUBMODULE", target, "initialize the already configured submodule", initialize)
            return

        if target.exists() or target.is_symlink():
            self.conflict(target, "existing skill path is not the expected Git submodule")
            return

        def add_submodule() -> Iterable[StateRecord]:
            self.assert_target(target)
            self.assert_target(self.project / ".gitmodules")
            result = self.git(self.project, "submodule", "add", upstream, relative)
            if result.returncode != 0 or not self.verified_submodule(target, upstream):
                raise RuntimeError(result.stderr.strip() or "submodule add could not be verified")
            return [StateRecord("skill-submodule", str(target), self.file_hash(target / "SKILL.md"), "submodule", upstream, "true")]

        self.add_operation("SUBMODULE", target, f"add {upstream}", add_submodule)

    def plan_stale(self) -> None:
        if not self.args.update:
            return
        active_sources = {self.source_id(path): path.name for path in self.agents_source.glob("*.toml")}
        active_names = set(active_sources.values())
        for record in self.state:
            legacy_name = record.source.replace("\\", "/").rsplit("/", 1)[-1]
            if record.kind == "agent" and record.source not in active_sources and legacy_name not in active_names:
                self.action("STALE", record.path, "previously managed role is no longer in this kit; no deletion was performed")

    def plan(self) -> None:
        if not self.state_path_safe:
            self.conflict(self.state_path, f"resolved state path escapes intended root {self.home}")
        self.plan_instructions()
        if not self.agents_source.is_dir():
            self.conflict(self.agents_source, "kit agent source directory is unavailable")
            source_agents: list[Path] = []
        else:
            source_agents = sorted(self.agents_source.glob("*.toml"))
        self.preflight_agent_catalog(source_agents)
        project_agents = self.agents_source.relative_to(self.root)
        target_agents = self.target / "agents" if self.args.scope == "global" else self.target / project_agents
        if self.require_target(target_agents):
            self.plan_agent_notices(target_agents, source_agents)
            for source in source_agents:
                try:
                    source.resolve().relative_to(self.agents_source)
                except ValueError:
                    self.conflict(source, "agent source resolves outside the configured source directory")
                    continue
                self.plan_file("agent", source, target_agents / source.name)
        self.plan_config()
        self.plan_overlap_notices()
        self.plan_skills()
        self.plan_stale()

    def commit(self) -> int:
        if self.choices:
            print("error: explicit --instructions choice is required", file=sys.stderr)
        if self.conflicts:
            print(f"warning: {self.conflicts} conflict(s) require review; preflight made no target changes", file=sys.stderr)
        if self.choices or self.conflicts:
            return 2
        if not self.args.apply:
            print("Dry run complete. Re-run with --apply after review.")
            return 0

        for record in self.pending_state:
            self.put_state(record)
        completed = 0
        failure: Exception | None = None
        for operation in self.operations:
            try:
                for record in operation.perform():
                    self.put_state(record)
                completed += 1
            except Exception as error:
                failure = error
                print(f"error: {operation.label} failed for {operation.path}: {error}", file=sys.stderr)
                break

        try:
            self.save_state()
        except OSError as error:
            print(f"error: could not save installer ownership state: {error}", file=sys.stderr)
            return 1
        if failure is not None:
            print(f"warning: apply stopped after {completed} completed operation(s); their ownership state was saved", file=sys.stderr)
            return 2
        print("Installation/update complete.")
        return 0

    def run(self) -> int:
        print(f"Lugui Codex Agent Kit {self.manifest['version']} | scope={self.args.scope} | mode={'apply' if self.args.apply else 'dry-run'}")
        print(f"Kit root: {self.root}\nTarget: {self.target}")
        self.plan()
        return self.commit()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("project", "global"), default="project")
    parser.add_argument("--target-project", default="")
    parser.add_argument("--instructions", choices=("ask", "append", "overwrite", "skip"), default="ask")
    parser.add_argument("--config", choices=("preserve", "merge", "replace"), default="merge")
    parser.add_argument("--global-defaults", choices=("preserve", "merge", "replace"), default="preserve")
    parser.add_argument("--skills", choices=("skip", "install"), default="skip")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("choose either --dry-run or --apply")
    if not args.apply:
        args.dry_run = True
    if args.scope == "global" and args.global_defaults != "preserve":
        args.config = args.global_defaults
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(Installer(parse_args(sys.argv[1:])).run())
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
