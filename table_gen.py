import re
import sys
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import List, Optional, Tuple, Dict

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel,
    QTextEdit, QLineEdit, QPushButton, QFileDialog, QMessageBox, QGroupBox,
    QPlainTextEdit, QTabWidget, QGridLayout
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

    idx_name = None
    idx_type = None
    idx_comment = None
    for i, h in enumerate(headers):
        h_lower = h.lower()
        if h_lower == 'column name':
            idx_name = i
        elif h_lower == 'data type':
            idx_type = i
        elif h_lower == 'comment':
            idx_comment = i

    # Если нет column name или data type, используем предположительные индексы
    if idx_type is None:
        idx_type = 1  # data type обычно второй
    if idx_name is None:
        idx_name = 0  # column name обычно первый

    # Индексы в строке данных (без первого столбца) на 1 меньше
    name_pos = idx_name - 1 if idx_name > 0 else None  # в строке данных нет отдельной колонки для имени
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
        hub_hashkey_name: Optional[str] = None,
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

        base = f"{self.prefix}_{self.domain}" if self.prefix else self.domain
        self.hub_hashkey_name = hub_hashkey_name or f"{base}_hashkey"
        self._validate()

    def _validate(self):
        src_names = {a.name for a in self.source_attrs}
        for bk in self.business_keys:
            if bk not in src_names:
                raise ValueError(f"Бизнес-ключ '{bk}' не найден в атрибутах источника")
        for pk in self.source_pk:
            if pk not in src_names:
                raise ValueError(f"Первичный ключ источника '{pk}' не найден в атрибутах источника")

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
        base_name = self.source_table.replace('v$', '').replace('tgo_', '')
        entity_name = f"tgo2_{base_name}"
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
        for bk in self.business_keys:
            src_attr = next(a for a in self.source_attrs if a.name == bk)
            hive_type, length, prec = self._map_pg_to_hive(src_attr)
            attrs.append(self._make_attr_dict(
                name=bk, pk_flag=True, typ=hive_type, length=length, prec=prec,
                desc=src_attr.comment, mandatory=True))
        id_attr = next((a for a in self.source_attrs if a.name == "id"), None)
        if id_attr:
            hive_type, length, prec = self._map_pg_to_hive(id_attr)
            attrs.append(self._make_attr_dict(
                name="id_pk_iar", pk_flag=False, typ=hive_type, length=length, prec=prec,
                desc=id_attr.comment, mandatory=False))
        for attr in self.source_attrs:
            if attr.name in self.business_keys or attr.name == "id":
                continue
            hive_type, length, prec = self._map_pg_to_hive(attr)
            attrs.append(self._make_attr_dict(
                name=attr.name, pk_flag=False, typ=hive_type, length=length, prec=prec,
                desc=attr.comment, mandatory=False))
        for tech in self._get_tech_fields(with_partition=True):
            is_part = tech.get("is_part", False)
            attrs.append(self._make_attr_dict(
                name=tech["name"], pk_flag=False, typ=tech["type"], length=0, prec=0,
                desc=tech["desc"], mandatory=True, tech=True, part=is_part))
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
        # Хэш-ключ – единый для хаба и сателлита
        attrs.append(self._make_attr_dict(
            name=self.hub_hashkey_name,
            pk_flag=True,
            typ="string",
            length=0,
            prec=0,
            desc=f"Хэш-ключ {self.desc_hub.lower()}",
            mandatory=True
        ))
        for bk in self.business_keys:
            src_attr = next(a for a in self.source_attrs if a.name == bk)
            hive_type, length, prec = self._map_pg_to_hive(src_attr)
            attrs.append(self._make_attr_dict(
                name=bk, pk_flag=False, typ=hive_type, length=length, prec=prec,
                desc=src_attr.comment, mandatory=False
            ))
        attrs.append(self._make_attr_dict(
            name="load_date", pk_flag=False, typ="timestamp", length=0, prec=0,
            desc="Дата загрузки", mandatory=False
        ))
        attrs.append(self._make_attr_dict(
            name="record_source", pk_flag=False, typ="string", length=0, prec=0,
            desc="Источник записи", mandatory=False
        ))
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
        for attr in self.source_attrs:
            if attr.name in self.business_keys:
                continue
            hive_type, length, prec = self._map_pg_to_hive(attr)
            name_in_sat = "id_pk_iar" if attr.name == "id" else attr.name
            attrs.append(self._make_attr_dict(
                name=name_in_sat, pk_flag=False, typ=hive_type, length=length, prec=prec,
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
                    length=38 if sa.hive_type == 'decimal' else 0,
                    prec=0, desc=sa.comment, mandatory=True))
        for sa in self.sur_attrs:
            if not sa.is_key:
                if sa.name == "tbl_part_col":
                    continue
                attrs.append(self._make_attr_dict(
                    name=sa.name, pk_flag=False, typ=sa.hive_type,
                    length=38 if sa.hive_type == 'decimal' else 0,
                    prec=0, desc=sa.comment, mandatory=False))
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

    # ------------------ Рендеринг YAML ------------------
    def _render_yaml_block(self, body: dict, is_source: bool = False) -> str:
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
        yaml.indent(mapping=2, sequence=2, offset=0)
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

    # ---- вкладка исходных данных ----
    def _create_source_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        layout.addWidget(QLabel("Текст таблицы (без заголовка):"))
        self.source_text = QTextEdit()
        self.source_text.setPlaceholderText("Вставьте строки данных (каждая запись в две строки)")
        self.source_text.setMinimumHeight(250)
        layout.addWidget(self.source_text)

        layout.addWidget(QLabel("Заголовок таблицы (будет добавлен сверху):"))
        self.source_header_edit = QLineEdit()
        self.source_header_edit.setText("column name\tdata type\tidentity\tcollation\tnot null\tdefault\tcomment")
        layout.addWidget(self.source_header_edit)

        layout.addWidget(QLabel("Бизнес-ключи (через запятую):"))
        self.source_business_keys = QLineEdit()
        self.source_business_keys.setPlaceholderText("например: punkt")
        layout.addWidget(self.source_business_keys)

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
        self.sur_header_edit.setText("column name\tdata type\tidentifier\tnot null\tcomment")
        layout.addWidget(self.sur_header_edit)

        layout.addWidget(QLabel("Бизнес-ключи для СУР (через запятую):"))
        self.sur_business_keys = QLineEdit()
        self.sur_business_keys.setPlaceholderText("например: punkt")
        layout.addWidget(self.sur_business_keys)

        layout.addStretch()
        return tab

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

        # Кнопка
        self.generate_btn = QPushButton("Сгенерировать и сохранить...")
        self.generate_btn.clicked.connect(self.generate_and_save)
        layout.addWidget(self.generate_btn)

        layout.addStretch()
        return tab

    def log(self, msg: str, error=False):
        prefix = "[ОШИБКА] " if error else "[INFO] "
        self.log_text.append(prefix + msg)

    def generate_and_save(self):
        # Сбор данных
        source_data = self.source_text.toPlainText().strip()
        sur_data = self.sur_text.toPlainText().strip()
        source_header = self.source_header_edit.text().strip()
        sur_header = self.sur_header_edit.text().strip()
        if source_header:
            source_data = source_header + "\n" + source_data
        if sur_header:
            sur_data = sur_header + "\n" + sur_data

        source_bk = [k.strip() for k in self.source_business_keys.text().split(",") if k.strip()]
        sur_bk = [k.strip() for k in self.sur_business_keys.text().split(",") if k.strip()]

        source_cp = self.source_cp.text().strip()
        source_ds = self.source_ds.text().strip()
        source_ds_desc = self.source_ds_desc.toPlainText().strip()
        if not source_cp:
            source_cp = "cp_[postgresql]_[pk_iar]_[tm]_[readwrite]"

        hive_cp = self.hive_cp.text().strip()
        hive_ds_snapshot = self.hive_ds_snapshot.text().strip()
        hive_ds_other = self.hive_ds_other.text().strip()
        hive_ds_desc_snapshot = self.hive_ds_desc_snapshot.toPlainText().strip()
        hive_ds_desc_other = self.hive_ds_desc_other.toPlainText().strip()
        if not hive_cp:
            hive_cp = "cp_[adh3_hive]_[dp_dsb]_[]_[]"

        project_prefix = self.project_prefix.text().strip()
        domain = self.domain_name.text().strip()
        source_table = self.source_table_name.text().strip()
        source_pk = [pk.strip() for pk in self.source_pk.text().split(",") if pk.strip()]
        if not source_pk:
            source_pk = ["id"]

        desc_source = self.desc_source.toPlainText().strip()
        desc_snapshot = self.desc_snapshot.toPlainText().strip()
        desc_staging = self.desc_staging.toPlainText().strip()
        desc_hub = self.desc_hub.toPlainText().strip()
        desc_sat = self.desc_sat.toPlainText().strip()
        desc_mart = self.desc_mart.toPlainText().strip()

        # Валидация
        if not source_data:
            QMessageBox.warning(self, "Ошибка", "Введите текст исходных данных")
            return
        if not sur_data:
            QMessageBox.warning(self, "Ошибка", "Введите текст СУР")
            return
        if not source_bk:
            QMessageBox.warning(self, "Ошибка", "Укажите бизнес-ключи для исходных данных")
            return
        if not source_table or not domain:
            QMessageBox.warning(self, "Ошибка", "Укажите имя исходной таблицы и домен")
            return

        # Парсинг
        try:
            source_attrs = parse_source_from_confluence(source_data)
            sur_attrs = parse_sur_from_confluence(sur_data)
            # Отладка: вывести первые комментарии
            self.log(f"source комментарии: {[(a.name, a.comment) for a in source_attrs[:5]]}")
            self.log(f"sur комментарии: {[(a.name, a.comment) for a in sur_attrs[:5]]}")
        except Exception as e:
            self.log(f"Ошибка парсинга: {e}", error=True)
            QMessageBox.critical(self, "Ошибка парсинга", str(e))
            return

        for sa in sur_attrs:
            sa.is_key = sa.name in sur_bk

        # Генератор
        try:
            generator = DataVaultYamlGenerator(
                domain_name=domain,
                project_prefix=project_prefix,
                source_table=source_table,
                source_pk=source_pk,
                business_keys=source_bk,
                source_attrs=source_attrs,
                sur_attrs=sur_attrs,
                source_cp=source_cp,
                source_ds=source_ds,
                source_ds_desc=source_ds_desc,
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
            )
        except Exception as e:
            self.log(f"Ошибка инициализации генератора: {e}", error=True)
            QMessageBox.critical(self, "Ошибка", str(e))
            return

        folder = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения YAML файлов")
        if not folder:
            return

        try:
            generator.save_all(folder)
            self.log(f"YAML файлы сохранены в {folder}")
            QMessageBox.information(self, "Готово", f"YAML файлы сохранены в папку:\n{folder}")
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