import re
import sys
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import List, Optional, Tuple, Dict

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel,
    QTextEdit, QLineEdit, QPushButton, QFileDialog, QMessageBox, QGroupBox,
    QPlainTextEdit, QTabWidget, QGridLayout, QCheckBox, QComboBox
)
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString


@dataclass
class SourceAttribute:
    name: str
    pg_type: str      # исходный тип PostgreSQL (например, 'int8', 'varchar', 'numeric')
    length: int = 0
    prec: int = 0
    comment: str = ""
    nullable: bool = True


@dataclass
class SurAttribute:
    name: str
    hive_type: str    # 'decimal', 'string', 'date', 'timestamp'
    comment: str = ""
    is_key: bool = False
    length: int = 0   # 'decimal' (precision)
    scale: int = 0  # 'decimal' (scale)


# ----------------------------------------------------------------------
# Парсеры текста из Confluence (двустрочный формат, колонки через табуляцию)
# ----------------------------------------------------------------------
def _parse_pg_type(type_str: str) -> Tuple[str, int, int]:
    """Извлекает базовый тип, длину и точность из строки типа PostgreSQL.
       Примеры: 'int8' -> ('int8',0,0); 'varchar(1000)' -> ('varchar',1000,0);
       'numeric(38,0)' -> ('numeric',38,0)
    """
    match = re.match(r'(\w+)(?:\((\d+)(?:,(\d+))?\))?', type_str)
    if not match:
        return 'text', 0, 0
    base = match.group(1)
    len_str = match.group(2)
    prec_str = match.group(3)
    length = int(len_str) if len_str else 0
    prec = int(prec_str) if prec_str else 0
    return base, length, prec


def parse_source_from_confluence(text: str) -> List[SourceAttribute]:
    lines = [line.rstrip('\n\r') for line in text.splitlines() if line.strip() != '']
    if not lines:
        return []
    header_line = lines[0]
    headers = [h.strip() for h in header_line.split('\t')]

    # Определяем индексы в заголовке
    idx_data_type = None
    idx_not_null = None
    idx_comment = None
    for i, h in enumerate(headers):
        h_lower = h.lower()
        if h_lower == 'data type':
            idx_data_type = i
        elif h_lower == 'not null':
            idx_not_null = i
        elif h_lower == 'comment':
            idx_comment = i

    if idx_data_type is None:
        raise ValueError("Не найдена колонка 'data type'")

    # Индексы в строке данных (без первого столбца) на 1 меньше
    data_type_pos = idx_data_type - 1
    not_null_pos = idx_not_null - 1 if idx_not_null is not None else None
    comment_pos = idx_comment - 1 if idx_comment is not None else None

    attrs = []
    i = 1
    while i < len(lines):
        name = lines[i].strip()
        if i + 1 >= len(lines):
            break
        data_line = lines[i+1]
        parts = data_line.split('\t')
        # Убедимся, что частей достаточно
        max_pos = max(p for p in (data_type_pos, not_null_pos, comment_pos) if p is not None)
        while len(parts) <= max_pos:
            parts.append('')
        pg_type_raw = parts[data_type_pos].strip()
        not_null_val = parts[not_null_pos].strip().lower() if not_null_pos is not None else 'false'
        comment = parts[comment_pos].strip() if comment_pos is not None else ''
        base_type, length, prec = _parse_pg_type(pg_type_raw)
        nullable = (not_null_val != 'true')
        attrs.append(SourceAttribute(
            name=name, pg_type=base_type, length=length, prec=prec,
            comment=comment, nullable=nullable))
        i += 2
    return attrs


def parse_sur_from_confluence(text: str) -> List[SurAttribute]:
    lines = [line.rstrip('\n\r') for line in text.splitlines() if line.strip() != '']
    if not lines:
        return []
    header_line = lines[0]
    headers = [h.strip() for h in header_line.split('\t')]

    idx_type = None
    idx_comment = None
    for i, h in enumerate(headers):
        h_lower = h.lower()
        if h_lower == 'data type':
            idx_type = i
        elif h_lower == 'comment':
            idx_comment = i

    # Если нет column name или data type, используем предположительные индексы
    if idx_type is None:
        idx_type = 1  # data type обычно второй

    # Индексы в строке данных (без первого столбца) на 1 меньше
    type_pos = idx_type - 1
    comment_pos = idx_comment - 1 if idx_comment is not None else None

    attrs = []
    i = 1
    while i < len(lines):
        name = lines[i].strip()
        if i + 1 >= len(lines):
            break
        data_line = lines[i+1]
        parts = data_line.split('\t')
        max_pos = max(p for p in (type_pos, comment_pos) if p is not None)
        while len(parts) <= max_pos:
            parts.append('')
        hive_type_raw = parts[type_pos].strip().upper()
        comment = parts[comment_pos].strip() if comment_pos is not None else ''
        # Нормализация типа
        if hive_type_raw in ('DECIMAL', 'NUMERIC'):
            hive_type = 'decimal'
        elif hive_type_raw in ('STRING', 'VARCHAR', 'TEXT'):
            hive_type = 'string'
        elif hive_type_raw == 'DATE':
            hive_type = 'date'
        elif hive_type_raw == 'TIMESTAMP':
            hive_type = 'timestamp'
        else:
            hive_type = 'string'
        attrs.append(SurAttribute(name=name, hive_type=hive_type, comment=comment, is_key=False))
        i += 2
    return attrs


# ----------------------------------------------------------------------
# Генератор YAML
# ----------------------------------------------------------------------
class DataVaultYamlGenerator:
    def __init__(
        self,
        domain_name: str,
        project_prefix: str,
        source_table: str,
        source_pk: List[str],
        business_keys: List[str],
        source_attrs: List[SourceAttribute],
        sur_attrs: List[SurAttribute],
        source_cp: str,
        source_ds: str,
        source_ds_desc: str,
        hive_cp: str,
        hive_ds_snapshot: str,
        hive_ds_other: str,
        hive_ds_desc_snapshot: str,
        hive_ds_desc_other: str,
        description_source: str,
        description_snapshot: str,
        description_staging: str,
        description_hub: str,
        description_sat: str,
        description_mart: str,
        surrogate_key_name: Optional[str] = None,
        skip_validation: bool = False
    ):
        self.domain = domain_name.lower()
        self.prefix = project_prefix.lower() if project_prefix else ""
        self.source_table = source_table
        self.source_pk = source_pk
        self.business_keys = business_keys
        self.source_attrs = source_attrs
        self.sur_attrs = sur_attrs

        self.source_cp = source_cp
        self.source_ds = source_ds
        self.source_ds_desc = source_ds_desc

        self.hive_cp = hive_cp
        self.hive_ds_snapshot = hive_ds_snapshot
        self.hive_ds_other = hive_ds_other
        self.hive_ds_desc_snapshot = hive_ds_desc_snapshot
        self.hive_ds_desc_other = hive_ds_desc_other

        self.desc_source = description_source
        self.desc_snapshot = description_snapshot
        self.desc_staging = description_staging
        self.desc_hub = description_hub
        self.desc_sat = description_sat
        self.desc_mart = description_mart

        # Имя первичного ключа источника (первый элемент списка)
        self.source_pk_name = source_pk[0] if source_pk else "id"
        # Имя, в которое переименовывается этот ключ в staging/sat (берём из бизнес-ключей СУР)
        self.surrogate_key_name = surrogate_key_name or "id_pk_iar"

        base = f"{self.prefix}_{self.domain}" if self.prefix else self.domain
        self.hub_hashkey_name = f"{base}_hashkey"
        self.skip_validation = skip_validation
        if not skip_validation:
            self._validate()

    def _validate(self):
        src_names = {a.name for a in self.source_attrs}
        for bk in self.business_keys:
            if bk not in src_names:
                raise ValueError(f"Бизнес-ключ '{bk}' не найден в атрибутах источника")
        if self.source_pk_name not in src_names:
            raise ValueError(f"Первичный ключ источника '{self.source_pk_name}' не найден в атрибутах источника")

    # ------------------ Вспомогательные методы ------------------
    @staticmethod
    def _map_pg_to_hive(attr: SourceAttribute) -> Tuple[str, int, int]:
        t = attr.pg_type.lower()
        if t.startswith("int") or t.startswith("numeric") or t == "decimal":
            return ("decimal", 38, 0)
        if t.startswith("varchar") or t == "text" or t.startswith("char"):
            return ("string", 0, 0)
        if t == "date":
            return ("date", 0, 0)
        if t == "timestamp":
            return ("timestamp", 0, 0)
        if t in ("bool", "boolean"):
            return ("boolean", 0, 0)
        return ("string", 0, 0)

    def _make_attr_dict(self, name, pk_flag, typ, length, prec, desc,
                        mandatory=False, tech=False, part=False, order=None):
        d = {
            "attrNme": name,
            "attrPkFlg": pk_flag,
            "attrTypNme": typ,
            "attrLen": length,
            "attrPrec": prec,
            "attrDesc": desc,
            "attrMandatoryFlg": mandatory,
        }
        if tech:
            d["attrTechFlg"] = True
        if part:
            d["attrPartColFlg"] = True
        if order is not None:
            d["attrPkOrderNum"] = order
        return d

    @staticmethod
    def _get_tech_fields(with_partition=False):
        tech = [
            {"name": "_tech_load_dt", "type": "timestamp", "desc": "Время начала исполнения Запуска задачи"},
            {"name": "_tech_exec_job_id", "type": "string", "desc": "Идентификатор Запуска задачи"},
        ]
        if with_partition:
            tech.append({"name": "tbl_part_col", "type": "string", "desc": "Поле секционирования", "is_part": True})
        tech.extend([
            {"name": "_tech_rls_wf_dt", "type": "timestamp", "desc": "Время открытия Экземпляра потока"},
            {"name": "_tech_rls_wf_inst_id", "type": "string", "desc": "Идентификатор Экземпляра потока"},
        ])
        return tech

    # ------------------ Построение тела сущностей ------------------
    def _build_source_body(self) -> dict:
        attrs = []
        for pk in self.source_pk:
            attr = next(a for a in self.source_attrs if a.name == pk)
            attrs.append(self._make_attr_dict(
                name=attr.name, pk_flag=True, typ=attr.pg_type, length=attr.length,
                prec=attr.prec, desc=attr.comment, mandatory=True))
        for attr in self.source_attrs:
            if attr.name in self.source_pk:
                continue
            attrs.append(self._make_attr_dict(
                name=attr.name, pk_flag=False, typ=attr.pg_type, length=attr.length,
                prec=attr.prec, desc=attr.comment, mandatory=False))
        return {
            "entityNme": self.source_table,
            "cpNmeUnq": self.source_cp,
            "dsNme": self.source_ds,
            "dsDesc": self.source_ds_desc,
            "detNmeUnq": "table",
            "destNmeUnq": "postgres",
            "ddmtNmeUnq": "source",
            "entityDesc": self.desc_source,
            "attributes": {"upsert": attrs},
        }

    def _build_snapshot_body(self) -> dict:
        attrs = []
        for attr in self.source_attrs:
            hive_type, length, prec = self._map_pg_to_hive(attr)
            pk_flag = attr.name in self.source_pk
            attrs.append(self._make_attr_dict(
                name=attr.name, pk_flag=pk_flag, typ=hive_type, length=length, prec=prec,
                desc=attr.comment, mandatory=pk_flag))
        for tech in self._get_tech_fields(with_partition=False):
            attrs.append(self._make_attr_dict(
                name=tech["name"], pk_flag=False, typ=tech["type"], length=0, prec=0,
                desc=tech["desc"], mandatory=True, tech=True))
        # Очищаем имя таблицы от схемы и префиксов
        raw_name = self.source_table.replace('v$', '').replace('tgo_', '')
        if '.' in raw_name:
            raw_name = raw_name.split('.')[-1]
        # Берём префикс из dsNme источника (например, "tgo" или "tgo2")
        prefix = self.source_ds.strip()
        entity_name = f"{prefix}_{raw_name}" if prefix else raw_name
        return {
            "entityNme": entity_name,
            "cpNmeUnq": self.hive_cp,
            "dsNme": self.hive_ds_snapshot,
            "dsDesc": self.hive_ds_desc_snapshot,
            "detNmeUnq": "table",
            "destNmeUnq": "hive_orc",
            "ddmtNmeUnq": "snapshot",
            "entityDesc": self.desc_snapshot,
            "attributes": {"upsert": attrs},
        }

    def _build_staging_body(self) -> dict:
        attrs = []

        # 1. Первичный ключ staging – переименованный source_pk_name
        pk_attr = next((a for a in self.source_attrs if a.name == self.source_pk_name), None)
        if pk_attr:
            hive_type, length, prec = self._map_pg_to_hive(pk_attr)
            attrs.append(self._make_attr_dict(
                name=self.surrogate_key_name,
                pk_flag=True,
                typ=hive_type,
                length=length,
                prec=prec,
                desc=pk_attr.comment,
                mandatory=True
            ))

        # 2. Остальные бизнес-ключи (исключая source_pk_name)
        for bk in self.business_keys:
            if bk == self.source_pk_name:
                continue
            src_attr = next((a for a in self.source_attrs if a.name == bk), None)
            if src_attr:
                hive_type, length, prec = self._map_pg_to_hive(src_attr)
                attrs.append(self._make_attr_dict(
                    name=bk, pk_flag=True, typ=hive_type, length=length, prec=prec,
                    desc=src_attr.comment, mandatory=True
                ))

        # 3. Все остальные атрибуты (не ключевые)
        for attr in self.source_attrs:
            if attr.name == self.source_pk_name or attr.name in self.business_keys:
                continue
            hive_type, length, prec = self._map_pg_to_hive(attr)
            attrs.append(self._make_attr_dict(
                name=attr.name, pk_flag=False, typ=hive_type, length=length, prec=prec,
                desc=attr.comment, mandatory=False
            ))

        # 4. Технические поля + секционирование
        for tech in self._get_tech_fields(with_partition=True):
            is_part = tech.get("is_part", False)
            attrs.append(self._make_attr_dict(
                name=tech["name"], pk_flag=False, typ=tech["type"], length=0, prec=0,
                desc=tech["desc"], mandatory=True, tech=True, part=is_part
            ))

        base = f"{self.prefix}_{self.domain}" if self.prefix else self.domain
        entity_name = f"staging_{base}"
        return {
            "entityNme": entity_name,
            "cpNmeUnq": self.hive_cp,
            "dsNme": self.hive_ds_other,
            "dsDesc": self.hive_ds_desc_other,
            "detNmeUnq": "table",
            "destNmeUnq": "hive_orc",
            "ddmtNmeUnq": "snapshotpartition",
            "entityDesc": self.desc_staging,
            "attributes": {"upsert": attrs},
        }

    def _build_hub_body(self) -> dict:
        attrs = []
        # Хэш-ключ
        attrs.append(self._make_attr_dict(
            name=self.hub_hashkey_name, pk_flag=True, typ="string", length=0, prec=0,
            desc=f"Хэш-ключ {self.desc_hub.lower()}", mandatory=True
        ))

        # Бизнес-ключ id_pk_iar (переименованный source_pk)
        pk_attr = next((a for a in self.source_attrs if a.name == self.source_pk_name), None)
        if pk_attr:
            hive_type, length, prec = self._map_pg_to_hive(pk_attr)
            attrs.append(self._make_attr_dict(
                name=self.surrogate_key_name,
                pk_flag=False,
                typ=hive_type,
                length=length,
                prec=prec,
                desc=pk_attr.comment,
                mandatory=False
            ))

        # Другие бизнес-ключи (если есть)
        for bk in self.business_keys:
            if bk == self.source_pk_name:
                continue
            src_attr = next((a for a in self.source_attrs if a.name == bk), None)
            if src_attr:
                hive_type, length, prec = self._map_pg_to_hive(src_attr)
                attrs.append(self._make_attr_dict(
                    name=bk, pk_flag=False, typ=hive_type, length=length, prec=prec,
                    desc=src_attr.comment, mandatory=False
                ))

        # Служебные поля
        attrs.append(self._make_attr_dict(
            name="load_date", pk_flag=False, typ="timestamp", length=0, prec=0,
            desc="Дата загрузки", mandatory=False
        ))
        attrs.append(self._make_attr_dict(
            name="record_source", pk_flag=False, typ="string", length=0, prec=0,
            desc="Источник записи", mandatory=False
        ))

        # Технические поля
        for tech in self._get_tech_fields(with_partition=False):
            attrs.append(self._make_attr_dict(
                name=tech["name"], pk_flag=False, typ=tech["type"], length=0, prec=0,
                desc=tech["desc"], mandatory=True, tech=True
            ))

        base = f"{self.prefix}_{self.domain}" if self.prefix else self.domain
        entity_name = f"domain_{base}_hub"
        return {
            "entityNme": entity_name,
            "cpNmeUnq": self.hive_cp,
            "dsNme": self.hive_ds_other,
            "dsDesc": self.hive_ds_desc_other,
            "detNmeUnq": "table",
            "destNmeUnq": "hive_orc",
            "ddmtNmeUnq": "factwithoutpartition",
            "entityDesc": self.desc_hub,
            "attributes": {"upsert": attrs},
        }

    def _build_sat_body(self) -> dict:
        attrs = []
        attrs.append(self._make_attr_dict(
            name=self.hub_hashkey_name, pk_flag=True, typ="string", length=0, prec=0,
            desc=f"Хэш-ключ {self.desc_hub.lower()}", mandatory=True))
        attrs.append(self._make_attr_dict(
            name="load_date", pk_flag=False, typ="timestamp", length=0, prec=0,
            desc="Дата загрузки", mandatory=False))
        attrs.append(self._make_attr_dict(
            name="record_source", pk_flag=False, typ="string", length=0, prec=0,
            desc="Источник записи", mandatory=False))
        attrs.append(self._make_attr_dict(
            name="hashdiff", pk_flag=False, typ="string", length=0, prec=0,
            desc="Хэш данных", mandatory=False))
        attrs.append(self._make_attr_dict(
            name="is_deleted", pk_flag=False, typ="boolean", length=0, prec=0,
            desc="Признак удаления записи", mandatory=False))

        # Все неключевые атрибуты (кроме source_pk_name и бизнес-ключей)
        for attr in self.source_attrs:
            if attr.name == self.source_pk_name or attr.name in self.business_keys:
                continue
            hive_type, length, prec = self._map_pg_to_hive(attr)
            # Оставляем исходное имя, не переименовываем
            attrs.append(self._make_attr_dict(
                name=attr.name, pk_flag=False, typ=hive_type, length=length, prec=prec,
                desc=attr.comment, mandatory=False))

        for tech in self._get_tech_fields(with_partition=False):
            attrs.append(self._make_attr_dict(
                name=tech["name"], pk_flag=False, typ=tech["type"], length=0, prec=0,
                desc=tech["desc"], mandatory=True, tech=True))

        base = f"{self.prefix}_{self.domain}" if self.prefix else self.domain
        entity_name = f"domain_{base}_sat"
        return {
            "entityNme": entity_name,
            "cpNmeUnq": self.hive_cp,
            "dsNme": self.hive_ds_other,
            "dsDesc": self.hive_ds_desc_other,
            "detNmeUnq": "table",
            "destNmeUnq": "hive_orc",
            "ddmtNmeUnq": "factwithoutpartition",
            "entityDesc": self.desc_sat,
            "attributes": {"upsert": attrs},
        }

    def _build_mart_body(self) -> dict:
        attrs = []
        for sa in self.sur_attrs:
            if sa.is_key:
                attrs.append(self._make_attr_dict(
                    name=sa.name, pk_flag=True, typ=sa.hive_type,
                    length=sa.length,
                    prec=sa.scale,
                    desc=sa.comment, mandatory=True))
        for sa in self.sur_attrs:
            if not sa.is_key:
                if sa.name == "tbl_part_col":
                    continue
                attrs.append(self._make_attr_dict(
                    name=sa.name, pk_flag=False, typ=sa.hive_type,
                    length=sa.length,
                    prec=sa.scale,
                    desc=sa.comment, mandatory=False))
        for tech in self._get_tech_fields(with_partition=True):
            is_part = tech.get("is_part", False)
            attrs.append(self._make_attr_dict(
                name=tech["name"], pk_flag=False, typ=tech["type"], length=0, prec=0,
                desc=tech["desc"], mandatory=True, tech=True, part=is_part))
        base = f"{self.prefix}_{self.domain}" if self.prefix else self.domain
        entity_name = f"mart_{base}"
        return {
            "entityNme": entity_name,
            "cpNmeUnq": self.hive_cp,
            "dsNme": self.hive_ds_other,
            "dsDesc": self.hive_ds_desc_other,
            "detNmeUnq": "table",
            "destNmeUnq": "hive_orc",
            "ddmtNmeUnq": "snapshotpartition",
            "entityDesc": self.desc_mart,
            "attributes": {"upsert": attrs},
        }

    # ------------------ Рендеринг YAML (исправленный) ------------------
    def _render_yaml_block(self, body: dict, is_source: bool = False) -> str:
        # Функция очистки от Ellipsis и None
        def sanitize(obj):
            if obj is Ellipsis:
                return "..."
            if obj is None:
                return ""
            if isinstance(obj, dict):
                return {k: sanitize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [sanitize(item) for item in obj]
            if isinstance(obj, (int, float, bool)):
                return obj
            return str(obj)

        # Проверка, что body – словарь (если нет, создаём пустой)
        if not isinstance(body, dict):
            body = {}

        body = sanitize(body)

        def wrap_strings(obj):
            if isinstance(obj, dict):
                return {k: wrap_strings(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [wrap_strings(item) for item in obj]
            if isinstance(obj, str):
                return DoubleQuotedScalarString(obj)
            return obj

        wrapped_body = wrap_strings(body)
        root = {"domain": "ias_kb", "body": wrapped_body}
        root["domain"] = DoubleQuotedScalarString("ias_kb")

        yaml = YAML()
        # mapping – отступ для пар ключ-значение (словарей) относительно родительского элемента.
        # sequence – отступ для элементов списка (- значение) относительно родительского ключа.
        # offset – дополнительный отступ для всех элементов (сдвиг вправо). Обычно используется для коррекции
        yaml.indent(mapping=2, sequence=2, offset=0)
        # TODO: баг лишним с отступом в блоке upsert
        yaml.default_flow_style = False
        stream = StringIO()
        yaml.dump(root, stream)
        yaml_str = stream.getvalue()

        lines = yaml_str.splitlines()
        if lines and lines[0].startswith("domain:"):
            if is_source:
                lines[0] = "# createIfNotExists entity \n" + lines[0]
            else:
                lines[0] = "# createIfNotExists entity <-_->\n" + lines[0]
        return "\n".join(lines)

    # ------------------ Публичные методы ------------------
    def generate_source(self) -> str:
        return self._render_yaml_block(self._build_source_body(), is_source=True)

    def generate_snapshot(self) -> str:
        return self._render_yaml_block(self._build_snapshot_body(), is_source=False)

    def generate_staging(self) -> str:
        return self._render_yaml_block(self._build_staging_body(), is_source=False)

    def generate_hub(self) -> str:
        return self._render_yaml_block(self._build_hub_body(), is_source=False)

    def generate_sat(self) -> str:
        return self._render_yaml_block(self._build_sat_body(), is_source=False)

    def generate_mart(self) -> str:
        return self._render_yaml_block(self._build_mart_body(), is_source=False)

    def generate_all(self) -> Dict[str, str]:
        return {
            "source": self.generate_source(),
            "snapshot": self.generate_snapshot(),
            "staging": self.generate_staging(),
            "hub": self.generate_hub(),
            "sat": self.generate_sat(),
            "mart": self.generate_mart(),
        }

    def save_all(self, output_dir: str):
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        for name, yaml_content in self.generate_all().items():
            file_path = out_path / f"{name}.yaml"
            file_path.write_text(yaml_content, encoding="utf-8")


# ----------------------------------------------------------------------
# GUI на PySide6
# ----------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Генератор YAML Data Vault")
        self.setMinimumSize(1100, 800)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Вкладки
        self.source_tab = self._create_source_tab()
        self.tabs.addTab(self.source_tab, "Исходные данные")
        self.sur_tab = self._create_sur_tab()
        self.tabs.addTab(self.sur_tab, "Данные СУР")
        self.config_tab = self._create_config_tab()
        self.tabs.addTab(self.config_tab, "Настройки генерации")
        self.log_tab = QWidget()
        log_layout = QVBoxLayout(self.log_tab)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        self.tabs.addTab(self.log_tab, "Лог")

    # ---- вкладка исходных данных
    def _create_source_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # Существующие виджеты Confluence (оставляем, но они будут скрываться при выборе CSV)
        self.source_text_label = QLabel("Текст таблицы (без заголовка):")
        self.source_text = QTextEdit()
        self.source_text.setPlaceholderText("Вставьте строки данных (каждая запись в две строки)")
        self.source_text.setMinimumHeight(250)

        self.source_header_label = QLabel("Заголовок таблицы (будет добавлен сверху):")
        self.source_header_edit = QLineEdit()
        self.source_header_edit.setText("column name\tdata type\tidentity\tcollation\tnot null\tdefault\tcomment")

        self.source_business_keys_label = QLabel("Бизнес-ключи (через запятую):")
        self.source_business_keys = QLineEdit()
        self.source_business_keys.setPlaceholderText("например: punkt")

        # Добавляем элементы в layout
        layout.addWidget(self.source_text_label)
        layout.addWidget(self.source_text)
        layout.addWidget(self.source_header_label)
        layout.addWidget(self.source_header_edit)
        layout.addWidget(self.source_business_keys_label)
        layout.addWidget(self.source_business_keys)

        # --- Блок для CSV
        self.source_input_mode = QComboBox()
        self.source_input_mode.addItems(["Confluence (двустрочный формат)", "CSV (таблица с колонками)"])
        self.source_input_mode.currentIndexChanged.connect(self.on_source_input_mode_changed)
        layout.addWidget(QLabel("Режим ввода исходных данных:"))
        layout.addWidget(self.source_input_mode)

        self.source_csv_text = QTextEdit()
        self.source_csv_text.setPlaceholderText(
            "Вставьте CSV-таблицу с разделителем табуляции или запятой.\n"
            "Ожидаемые колонки: Column Name, Data Type, Not Null, Comment, Length, Scale"
        )
        self.source_csv_load_btn = QPushButton("Загрузить CSV из файла")
        self.source_csv_load_btn.clicked.connect(self.load_source_csv_file)
        self.source_csv_load_btn.hide()
        self.source_csv_text.setMinimumHeight(250)
        self.source_csv_text.hide()

        layout.addWidget(self.source_csv_load_btn)
        layout.addWidget(self.source_csv_text)

        # Группа параметров подключения source (PostgreSQL) остаётся без изменений
        group_source = QGroupBox("Параметры подключения source (PostgreSQL)")
        src_layout = QGridLayout()
        src_layout.addWidget(QLabel("cpNmeUnq:"), 0, 0)
        self.source_cp = QLineEdit("cp_[postgresql]_[pk_iar]_[tm]_[readwrite]")
        src_layout.addWidget(self.source_cp, 0, 1)
        src_layout.addWidget(QLabel("dsNme:"), 1, 0)
        self.source_ds = QLineEdit("tgo2")
        src_layout.addWidget(self.source_ds, 1, 1)
        src_layout.addWidget(QLabel("dsDesc:"), 2, 0)
        self.source_ds_desc = QPlainTextEdit()
        self.source_ds_desc.setPlainText("Cхема TGO2. БД PostgreSQL «ПК ИАР»")
        self.source_ds_desc.setMinimumHeight(80)
        src_layout.addWidget(self.source_ds_desc, 2, 1)
        group_source.setLayout(src_layout)
        layout.addWidget(group_source)

        layout.addStretch()
        return tab

    def on_source_input_mode_changed(self, index):
        if index == 0:  # Confluence
            self.source_csv_load_btn.hide()
            self.source_csv_text.hide()
            self.source_text_label.show()
            self.source_text.show()
            self.source_header_label.show()
            self.source_header_edit.show()
            self.source_business_keys_label.show()
            self.source_business_keys.show()
        else:  # CSV
            self.source_csv_load_btn.show()
            self.source_csv_text.show()
            self.source_text_label.hide()
            self.source_text.hide()
            self.source_header_label.hide()
            self.source_header_edit.hide()
            self.source_business_keys_label.show()  # бизнес‑ключи нужны всегда
            self.source_business_keys.show()

    def load_source_csv_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Выберите CSV-файл", "", "CSV files (*.csv);;All files (*.*)")
        if not file_path:
            return
        with open(file_path, 'rb') as f:
            raw_data = f.read()
        encodings = ['utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be', 'cp1251', 'latin-1']
        content = None
        used_encoding = None
        for enc in encodings:
            try:
                content = raw_data.decode(enc)
                used_encoding = enc
                break
            except UnicodeDecodeError:
                continue
        if content is None:
            QMessageBox.critical(self, "Ошибка", "Не удалось определить кодировку файла.")
            return
        self.log(f"CSV исходных данных загружен, кодировка {used_encoding}, размер {len(content)} символов.")
        self.source_csv_text.setPlainText(content)

    def parse_source_csv(self, csv_text: str) -> List[SourceAttribute]:
        lines = [line.strip() for line in csv_text.splitlines() if line.strip()]
        if not lines:
            raise ValueError("CSV текст пуст.")
        # Определяем разделитель
        delim = '\t' if '\t' in lines[0] else ',' if ',' in lines[0] else ';'
        headers = [h.strip() for h in lines[0].split(delim)]

        # Ищем индексы
        def find(col):
            for i, h in enumerate(headers):
                if h.lower() == col.lower():
                    return i
            return None

        idx_name = find('column name')
        idx_type = find('data type')
        idx_notnull = find('not null')
        idx_comment = find('description')
        idx_len = find('length')
        idx_scale = find('scale')
        if idx_name is None or idx_type is None:
            raise ValueError("CSV должен содержать колонки 'Column Name' и 'Data Type'")
        attrs = []
        for row_num, row in enumerate(lines[1:], start=2):
            parts = row.split(delim)
            if len(parts) <= max(idx_name, idx_type, idx_notnull or 0, idx_comment or 0, idx_len or 0, idx_scale or 0):
                continue
            name = parts[idx_name].strip()
            if not name:
                continue
            data_type = parts[idx_type].strip().lower()
            not_null = parts[idx_notnull].strip().lower() if idx_notnull is not None else 'false'
            comment = parts[idx_comment].strip() if idx_comment is not None else ''
            length_str = parts[idx_len].strip() if idx_len is not None and idx_len < len(parts) else ''
            scale_str = parts[idx_scale].strip() if idx_scale is not None and idx_scale < len(parts) else ''
            # Формируем тип PostgreSQL
            if data_type == 'decimal':
                if length_str and length_str != '[null]':
                    try:
                        length = int(length_str)
                        scale = int(scale_str) if scale_str and scale_str != '[null]' else 0
                        pg_type = f"numeric({length},{scale})"
                    except:
                        pg_type = "numeric(38,0)"
                else:
                    pg_type = "numeric(38,0)"
            elif data_type in ('int', 'int4', 'integer'):
                pg_type = "int4"
            elif data_type in ('int8', 'bigint'):
                pg_type = "int8"
            elif data_type.startswith('varchar'):
                if length_str and length_str != '[null]':
                    pg_type = f"varchar({length_str})"
                else:
                    pg_type = "varchar"
            elif data_type == 'date':
                pg_type = "date"
            elif data_type == 'timestamp':
                pg_type = "timestamp"
            else:
                pg_type = data_type
            # Извлекаем длину/точность для атрибута (нужны для маппинга в Hive)
            if 'numeric' in pg_type:
                import re
                m = re.search(r'\((\d+),(\d+)\)', pg_type)
                if m:
                    length = int(m.group(1))
                    prec = int(m.group(2))
                else:
                    length, prec = 38, 0
            elif 'varchar' in pg_type:
                m = re.search(r'\((\d+)\)', pg_type)
                length = int(m.group(1)) if m else 0
                prec = 0
            else:
                length, prec = 0, 0
            attrs.append(SourceAttribute(
                name=name,
                pg_type=pg_type,
                length=length,
                prec=prec,
                comment=comment,
                nullable=(not_null != 'true')
            ))
        if not attrs:
            raise ValueError("Не удалось извлечь ни одного атрибута из CSV.")
        self.log(f"Извлечено {len(attrs)} атрибутов исходных данных из CSV.")
        return attrs

    # ---- вкладка СУР ----
    def _create_sur_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        layout.addWidget(QLabel("Текст таблицы (без заголовка):"))
        self.sur_text = QTextEdit()
        self.sur_text.setPlaceholderText("Вставьте строки данных (каждая запись в две строки)")
        self.sur_text.setMinimumHeight(250)
        layout.addWidget(self.sur_text)

        layout.addWidget(QLabel("Заголовок таблицы:"))
        self.sur_header_edit = QLineEdit()
        self.sur_header_edit.setText("column name\tdata type\tidentity\tcollation\tnot null\tdefault\tcomment")
        layout.addWidget(self.sur_header_edit)

        layout.addWidget(QLabel("Бизнес-ключи для СУР (через запятую):"))
        self.sur_business_keys = QLineEdit()
        self.sur_business_keys.setPlaceholderText("например: punkt")
        layout.addWidget(self.sur_business_keys)

        # Переключатель режимов ввода
        self.sur_input_mode = QComboBox()
        self.sur_input_mode.addItems(["Confluence (двустрочный формат)", "CSV (таблица с колонками)"])
        self.sur_input_mode.currentIndexChanged.connect(self.on_sur_input_mode_changed)
        layout.addWidget(QLabel("Режим ввода данных СУР:"))
        layout.addWidget(self.sur_input_mode)

        # Поле для CSV (изначально скрыто)
        self.sur_csv_text = QTextEdit()
        self.sur_csv_text.setPlaceholderText(
            "Вставьте CSV-таблицу с разделителем табуляции или запятой.\n"
            "Ожидаемые колонки: Column Name, Not Null, Data Type, Description, Length, Scale"
        )
        self.sur_csv_load_btn = QPushButton("Загрузить CSV из файла")
        self.sur_csv_load_btn.clicked.connect(self.load_sur_csv_file)
        self.sur_csv_load_btn.hide()  # изначально скрыта, показываем только в CSV-режиме
        layout.addWidget(self.sur_csv_load_btn)

        self.sur_csv_text.setMinimumHeight(250)
        self.sur_csv_text.hide()
        layout.addWidget(self.sur_csv_text)

        # Сохраняем ссылки на виджеты Confluence, чтобы их скрывать/показывать
        self.sur_text_label = QLabel("Текст таблицы (без заголовка):")
        self.sur_header_label = QLabel("Заголовок таблицы (будет добавлен сверху):")

        layout.addStretch()
        return tab

    def on_sur_input_mode_changed(self, index):
        if index == 0:  # Confluence
            self.sur_csv_load_btn.hide()
            self.sur_text_label.show()
            self.sur_text.show()
            self.sur_header_label.show()
            self.sur_header_edit.show()
            self.sur_csv_text.hide()
        else:  # CSV
            self.sur_csv_load_btn.show()
            self.sur_text_label.hide()
            self.sur_text.hide()
            self.sur_header_label.hide()
            self.sur_header_edit.hide()
            self.sur_csv_text.show()

    def load_sur_csv_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Выберите CSV-файл", "", "CSV files (*.csv);;All files (*.*)")
        if not file_path:
            return

        # Читаем файл в бинарном виде и пробуем разные кодировки
        with open(file_path, 'rb') as f:
            raw_data = f.read()

        # Список кодировок в порядке приоритета
        encodings = ['utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be', 'cp1251', 'latin-1']
        content = None
        used_encoding = None
        for enc in encodings:
            try:
                content = raw_data.decode(enc)
                used_encoding = enc
                break
            except UnicodeDecodeError:
                continue

        if content is None:
            QMessageBox.critical(self, "Ошибка",
                                 "Не удалось определить кодировку файла. Попробуйте сохранить CSV в UTF-8.")
            return

        self.log(f"CSV файл загружен, кодировка {used_encoding}, размер {len(content)} символов.")
        # Отображаем первые 200 символов для диагностики
        self.log(f"Первые 200 символов:\n{content[:200]}")
        self.sur_csv_text.setPlainText(content)

    # ---- вкладка настроек ----
    def _create_config_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # Основные параметры
        group_basic = QGroupBox("Основные параметры")
        basic_layout = QGridLayout()
        basic_layout.addWidget(QLabel("Префикс проекта (например 'cl'):"), 0, 0)
        self.project_prefix = QLineEdit("cl")
        basic_layout.addWidget(self.project_prefix, 0, 1)
        basic_layout.addWidget(QLabel("Доменное имя (без префикса):"), 1, 0)
        self.domain_name = QLineEdit("delivery_points_station_codes")
        basic_layout.addWidget(self.domain_name, 1, 1)
        basic_layout.addWidget(QLabel("Имя исходной таблицы (source):"), 2, 0)
        self.source_table_name = QLineEdit("v$spr_stations")
        basic_layout.addWidget(self.source_table_name, 2, 1)
        basic_layout.addWidget(QLabel("Первичный ключ источника (через запятую):"), 3, 0)
        self.source_pk = QLineEdit("punkt")
        basic_layout.addWidget(self.source_pk, 3, 1)
        group_basic.setLayout(basic_layout)
        layout.addWidget(group_basic)

        # Параметры Hive-таблиц
        group_hive = QGroupBox("Параметры Hive-таблиц")
        hive_layout = QGridLayout()
        hive_layout.addWidget(QLabel("cpNmeUnq (общий):"), 0, 0)
        self.hive_cp = QLineEdit("cp_[adh3_hive]_[dp_dsb]_[]_[]")
        hive_layout.addWidget(self.hive_cp, 0, 1)
        hive_layout.addWidget(QLabel("dsNme (snapshot):"), 1, 0)
        self.hive_ds_snapshot = QLineEdit("dl_pk_iar")
        hive_layout.addWidget(self.hive_ds_snapshot, 1, 1)
        hive_layout.addWidget(QLabel("dsNme (staging/hub/sat/mart):"), 2, 0)
        self.hive_ds_other = QLineEdit("dl_iascb_tgo5")
        hive_layout.addWidget(self.hive_ds_other, 2, 1)
        hive_layout.addWidget(QLabel("dsDesc (snapshot):"), 3, 0)
        self.hive_ds_desc_snapshot = QPlainTextEdit()
        self.hive_ds_desc_snapshot.setPlainText("Схема «ПК ИАР». БД Hive Блока хранения данных Платформы данных.")
        self.hive_ds_desc_snapshot.setMinimumHeight(60)
        hive_layout.addWidget(self.hive_ds_desc_snapshot, 3, 1)
        hive_layout.addWidget(QLabel("dsDesc (staging/hub/sat/mart):"), 4, 0)
        self.hive_ds_desc_other = QPlainTextEdit()
        self.hive_ds_desc_other.setPlainText("Схема Риски ТГО5. БД Hive Блока хранения данных Платформы данных.")
        self.hive_ds_desc_other.setMinimumHeight(60)
        hive_layout.addWidget(self.hive_ds_desc_other, 4, 1)
        group_hive.setLayout(hive_layout)
        layout.addWidget(group_hive)

        # Описания сущностей
        group_desc = QGroupBox("Описания сущностей")
        desc_layout = QGridLayout()
        desc_layout.addWidget(QLabel("source:"), 0, 0)
        self.desc_source = QPlainTextEdit()
        self.desc_source.setPlainText("Справочник пунктов поставок и кодов станций")
        desc_layout.addWidget(self.desc_source, 0, 1)
        desc_layout.addWidget(QLabel("snapshot:"), 1, 0)
        self.desc_snapshot = QPlainTextEdit()
        self.desc_snapshot.setPlainText("Справочник пунктов поставок и кодов станций")
        desc_layout.addWidget(self.desc_snapshot, 1, 1)
        desc_layout.addWidget(QLabel("staging:"), 2, 0)
        self.desc_staging = QPlainTextEdit()
        self.desc_staging.setPlainText("Промежуточный слой для справочника пунктов поставок и кодов станций")
        desc_layout.addWidget(self.desc_staging, 2, 1)
        desc_layout.addWidget(QLabel("hub:"), 3, 0)
        self.desc_hub = QPlainTextEdit()
        self.desc_hub.setPlainText("Хаб для справочника пунктов поставок и кодов станций")
        desc_layout.addWidget(self.desc_hub, 3, 1)
        desc_layout.addWidget(QLabel("sat:"), 4, 0)
        self.desc_sat = QPlainTextEdit()
        self.desc_sat.setPlainText("Спутник для справочника пунктов поставок и кодов станций")
        desc_layout.addWidget(self.desc_sat, 4, 1)
        desc_layout.addWidget(QLabel("mart:"), 5, 0)
        self.desc_mart = QPlainTextEdit()
        self.desc_mart.setPlainText("Витрина справочника пунктов поставок и кодов станций")
        desc_layout.addWidget(self.desc_mart, 5, 1)
        group_desc.setLayout(desc_layout)
        layout.addWidget(group_desc)

        # Группа выбора генерируемых сущностей
        group_entities = QGroupBox("Генерируемые сущности")
        entities_layout = QGridLayout()
        self.cb_source = QCheckBox("source")
        self.cb_snapshot = QCheckBox("snapshot")
        self.cb_staging = QCheckBox("staging")
        self.cb_hub = QCheckBox("hub")
        self.cb_sat = QCheckBox("sat")
        self.cb_mart = QCheckBox("mart")
        # По умолчанию все включены
        self.cb_source.setChecked(True)
        self.cb_snapshot.setChecked(True)
        self.cb_staging.setChecked(True)
        self.cb_hub.setChecked(True)
        self.cb_sat.setChecked(True)
        self.cb_mart.setChecked(True)

        entities_layout.addWidget(self.cb_source, 0, 0)
        entities_layout.addWidget(self.cb_snapshot, 0, 1)
        entities_layout.addWidget(self.cb_staging, 0, 2)
        entities_layout.addWidget(self.cb_hub, 1, 0)
        entities_layout.addWidget(self.cb_sat, 1, 1)
        entities_layout.addWidget(self.cb_mart, 1, 2)
        group_entities.setLayout(entities_layout)
        layout.addWidget(group_entities)

        # Кнопка
        self.generate_btn = QPushButton("Сгенерировать и сохранить...")
        self.generate_btn.clicked.connect(self.generate_and_save)
        layout.addWidget(self.generate_btn)

        layout.addStretch()
        return tab

    def log(self, msg: str, error=False):
        prefix = "[ОШИБКА] " if error else "[INFO] "
        self.log_text.append(prefix + msg)

    def parse_sur_csv(self, csv_text: str) -> List[SurAttribute]:
        lines = [line.strip() for line in csv_text.splitlines() if line.strip()]
        if not lines:
            self.log("CSV текст пуст.", error=True)
            raise ValueError("CSV текст пуст.")

        # Пробуем разные разделители
        possible_delimiters = ['\t', ',', ';', '|']
        delimiter = None
        for delim in possible_delimiters:
            if delim in lines[0]:
                delimiter = delim
                break
        if delimiter is None:
            self.log(
                "Не удалось определить разделитель. Попробуйте использовать табуляцию, запятую или точку с запятой.",
                error=True)
            raise ValueError("Не удалось определить разделитель.")

        # Разбиваем заголовок
        headers = [h.strip() for h in lines[0].split(delimiter)]
        self.log(f"Найден разделитель: '{delimiter}', заголовки: {headers}")

        # Поиск индексов (регистронезависимо)
        def find_idx(col):
            for i, h in enumerate(headers):
                if h.lower() == col.lower():
                    return i
            return None

        idx_name = find_idx('column name')
        idx_notnull = find_idx('not null')
        idx_type = find_idx('data type')
        idx_desc = find_idx('description')
        idx_len = find_idx('length')
        idx_scale = find_idx('scale')

        # Если не нашли все необходимые колонки, выводим диагностику
        missing = []
        if idx_name is None: missing.append('Column Name')
        if idx_type is None: missing.append('Data Type')
        if missing:
            self.log(f"Отсутствуют обязательные колонки: {', '.join(missing)}", error=True)
            raise ValueError(f"CSV не содержит колонок: {', '.join(missing)}")

        attrs = []
        for row_num, row in enumerate(lines[1:], start=2):
            length = 0
            scale = 0
            parts = row.split(delimiter)
            # Если частей меньше, чем максимальный индекс, пропускаем строку
            max_needed = max(idx_name, idx_type, idx_notnull or 0, idx_desc or 0, idx_len or 0, idx_scale or 0)
            if len(parts) <= max_needed:
                self.log(f"Строка {row_num} пропущена (недостаточно колонок: {len(parts)} вместо {max_needed + 1})")
                continue

            name = parts[idx_name].strip()
            if not name:
                continue

            hive_type_raw = parts[idx_type].strip().upper()
            description = parts[idx_desc].strip() if idx_desc is not None and idx_desc < len(parts) else ''
            length_str = parts[idx_len].strip() if idx_len is not None and idx_len < len(parts) else ''
            scale_str = parts[idx_scale].strip() if idx_scale is not None and idx_scale < len(parts) else ''

            # Формируем тип Hive
            if hive_type_raw == 'DECIMAL':
                hive_type = 'decimal'
                if length_str and length_str != '[NULL]':
                    try:
                        length = int(length_str)
                        scale = int(scale_str) if scale_str and scale_str != '[NULL]' else 0
                    except ValueError:
                        length = 38
                        scale = 0
                else:
                    length = 38
                    scale = 0
            elif hive_type_raw == 'STRING':
                hive_type = 'string'
            elif hive_type_raw == 'DATE':
                hive_type = 'date'
            elif hive_type_raw == 'TIMESTAMP':
                hive_type = 'timestamp'
            else:
                hive_type = 'string'

            attrs.append(SurAttribute(
                name=name,
                hive_type=hive_type,
                comment=description,
                is_key=False,
                length=length,
                scale=scale
            ))

        if not attrs:
            self.log("Не удалось извлечь ни одного атрибута. Проверьте соответствие колонок и разделитель.", error=True)
            raise ValueError("Не удалось извлечь ни одного атрибута. Проверьте формат CSV.")
        self.log(f"Успешно извлечено {len(attrs)} атрибутов из CSV.")
        return attrs

    def generate_and_save(self):
        # Определяем, какие сущности нужно генерировать
        gen_source = self.cb_source.isChecked()
        gen_snapshot = self.cb_snapshot.isChecked()
        gen_staging = self.cb_staging.isChecked()
        gen_hub = self.cb_hub.isChecked()
        gen_sat = self.cb_sat.isChecked()
        gen_mart = self.cb_mart.isChecked()

        if not any([gen_source, gen_snapshot, gen_staging, gen_hub, gen_sat, gen_mart]):
            QMessageBox.warning(self, "Ошибка", "Не выбрано ни одной сущности для генерации")
            return

        need_source_data = gen_source or gen_snapshot or gen_staging or gen_hub or gen_sat

        # Инициализируем переменные
        source_attrs = []
        sur_attrs = []

        # --- Парсинг исходных данных, если нужны source-сущности
        if need_source_data:
            # Определяем, какой режим ввода для исходных данных
            if self.source_input_mode.currentIndex() == 0:  # Confluence
                source_text = self.source_text.toPlainText().strip()
                source_header = self.source_header_edit.text().strip()
                if source_header:
                    source_text = source_header + "\n" + source_text
                if not source_text:
                    QMessageBox.warning(self, "Ошибка",
                                        "Для выбранных сущностей необходимо ввести текст исходных данных")
                    return
                try:
                    source_attrs = parse_source_from_confluence(source_text)
                except Exception as e:
                    self.log(f"Ошибка парсинга исходных данных: {e}", error=True)
                    QMessageBox.critical(self, "Ошибка парсинга", str(e))
                    return
            else:  # CSV
                source_csv = self.source_csv_text.toPlainText().strip()
                if not source_csv:
                    QMessageBox.warning(self, "Ошибка",
                                        "Для выбранных сущностей необходимо ввести CSV данные")
                    return
                try:
                    source_attrs = self.parse_source_csv(source_csv)
                except Exception as e:
                    self.log(f"Ошибка парсинга CSV исходных данных: {e}", error=True)
                    QMessageBox.critical(self, "Ошибка парсинга", str(e))
                    return

        # --- Парсинг данных СУР, если нужен mart
        if gen_mart:
            if self.sur_input_mode.currentIndex() == 0:  # Confluence
                sur_text = self.sur_text.toPlainText().strip()
                sur_header = self.sur_header_edit.text().strip()
                if sur_header:
                    sur_text = sur_header + "\n" + sur_text
                if not sur_text:
                    QMessageBox.warning(self, "Ошибка", "Для генерации mart необходимо ввести текст СУР")
                    return
                try:
                    sur_attrs = parse_sur_from_confluence(sur_text)
                except Exception as e:
                    self.log(f"Ошибка парсинга СУР: {e}", error=True)
                    QMessageBox.critical(self, "Ошибка парсинга", str(e))
                    return
            else:  # CSV
                sur_csv = self.sur_csv_text.toPlainText().strip()
                if not sur_csv:
                    QMessageBox.warning(self, "Ошибка", "Для генерации mart необходимо ввести CSV данные")
                    return
                try:
                    sur_attrs = self.parse_sur_csv(sur_csv)
                except Exception as e:
                    self.log(f"Ошибка парсинга CSV СУР: {e}", error=True)
                    QMessageBox.critical(self, "Ошибка парсинга", str(e))
                    return

            if not sur_attrs:
                QMessageBox.critical(self, "Ошибка", "Не удалось извлечь атрибуты из данных СУР. Проверьте ввод.")
                return

        # Бизнес-ключи
        source_bk = [k.strip() for k in self.source_business_keys.text().split(",") if k.strip()]
        sur_bk = [k.strip() for k in self.sur_business_keys.text().split(",") if k.strip()]

        # Параметры source (PostgreSQL)
        source_cp = self.source_cp.text().strip()
        source_ds = self.source_ds.text().strip()
        source_ds_desc = self.source_ds_desc.toPlainText().strip()
        if not source_cp:
            source_cp = "cp_[postgresql]_[pk_iar]_[tm]_[readwrite]"

        # Параметры Hive
        hive_cp = self.hive_cp.text().strip()
        hive_ds_snapshot = self.hive_ds_snapshot.text().strip()
        hive_ds_other = self.hive_ds_other.text().strip()
        hive_ds_desc_snapshot = self.hive_ds_desc_snapshot.toPlainText().strip()
        hive_ds_desc_other = self.hive_ds_desc_other.toPlainText().strip()
        if not hive_cp:
            hive_cp = "cp_[adh3_hive]_[dp_dsb]_[]_[]"

        # Имена и префиксы
        project_prefix = self.project_prefix.text().strip()
        domain = self.domain_name.text().strip()
        source_table = self.source_table_name.text().strip()
        source_pk = [pk.strip() for pk in self.source_pk.text().split(",") if pk.strip()]
        if not source_pk:
            source_pk = ["id"]

        # Описания сущностей
        desc_source = self.desc_source.toPlainText().strip()
        desc_snapshot = self.desc_snapshot.toPlainText().strip()
        desc_staging = self.desc_staging.toPlainText().strip()
        desc_hub = self.desc_hub.toPlainText().strip()
        desc_sat = self.desc_sat.toPlainText().strip()
        desc_mart = self.desc_mart.toPlainText().strip()

        if need_source_data and (not source_table or not domain):
            QMessageBox.warning(self, "Ошибка", "Укажите имя исходной таблицы и домен")
            return

        # Установка бизнес-ключей для СУР
        for sa in sur_attrs:
            sa.is_key = sa.name in sur_bk

        surrogate_key_name = sur_bk[0] if sur_bk else "id_pk_iar"

        try:
            generator = DataVaultYamlGenerator(
                domain_name=domain if need_source_data else "dummy",
                project_prefix=project_prefix if need_source_data else "",
                source_table=source_table if need_source_data else "",
                source_pk=source_pk if need_source_data else [],
                business_keys=source_bk if need_source_data else [],
                source_attrs=source_attrs,
                sur_attrs=sur_attrs,
                source_cp=source_cp if need_source_data else "",
                source_ds=source_ds if need_source_data else "",
                source_ds_desc=source_ds_desc if need_source_data else "",
                hive_cp=hive_cp,
                hive_ds_snapshot=hive_ds_snapshot,
                hive_ds_other=hive_ds_other,
                hive_ds_desc_snapshot=hive_ds_desc_snapshot,
                hive_ds_desc_other=hive_ds_desc_other,
                description_source=desc_source,
                description_snapshot=desc_snapshot,
                description_staging=desc_staging,
                description_hub=desc_hub,
                description_sat=desc_sat,
                description_mart=desc_mart,
                surrogate_key_name=surrogate_key_name,
                skip_validation=not need_source_data
            )
        except Exception as e:
            self.log(f"Ошибка инициализации генератора: {e}", error=True)
            QMessageBox.critical(self, "Ошибка", str(e))
            return

        folder = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения YAML файлов")
        if not folder:
            return

        try:
            out_path = Path(folder)
            out_path.mkdir(parents=True, exist_ok=True)
            if gen_source:
                out_path.joinpath("source.yaml").write_text(generator.generate_source(), encoding="utf-8")
            if gen_snapshot:
                out_path.joinpath("snapshot.yaml").write_text(generator.generate_snapshot(), encoding="utf-8")
            if gen_staging:
                out_path.joinpath("staging.yaml").write_text(generator.generate_staging(), encoding="utf-8")
            if gen_hub:
                out_path.joinpath("hub.yaml").write_text(generator.generate_hub(), encoding="utf-8")
            if gen_sat:
                out_path.joinpath("sat.yaml").write_text(generator.generate_sat(), encoding="utf-8")
            if gen_mart:
                out_path.joinpath("mart.yaml").write_text(generator.generate_mart(), encoding="utf-8")
            self.log(f"YAML файлы сохранены в {folder}")
            QMessageBox.information(self, "Готово", f"Выбранные YAML файлы сохранены в папку:\n{folder}")
        except Exception as e:
            self.log(f"Ошибка сохранения: {e}", error=True)
            QMessageBox.critical(self, "Ошибка", str(e))


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()