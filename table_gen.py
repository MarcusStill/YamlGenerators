import sys
import re
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QLineEdit, QPushButton, QFileDialog, QMessageBox, QGroupBox,
    QFormLayout
)


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
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from io import StringIO

class DataVaultYamlGenerator:
    def __init__(
        self,
        domain_name: str,
        source_table: str,
        source_pk: List[str],
        business_keys: List[str],
        source_attrs: List[SourceAttribute],
        sur_attrs: List[SurAttribute],
        description: str = "",
        hub_hashkey_name: Optional[str] = None,
    ):
        self.domain = domain_name.lower()
        self.source_table = source_table
        self.source_pk = source_pk
        self.business_keys = business_keys
        self.source_attrs = source_attrs
        self.sur_attrs = sur_attrs
        self.description = description
        self.hub_hashkey_name = hub_hashkey_name or f"{self.domain}_hashkey"
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

    # ------------------ Методы построения тела сущностей ------------------
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
            "cpNmeUnq": "cp_[postgresql]_[pk_iar]_[tm]_[readwrite]",
            "dsNme": "dl_pk_iar",
            "dsDesc": "Схема dl_pk_iar. БД PostgreSQL «ПК ИАР»",
            "detNmeUnq": "table",
            "destNmeUnq": "postgres",
            "ddmtNmeUnq": "source",
            "entityDesc": self.description,
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
        entity_name = f"tgo2_{self.source_table.replace('v$', '').replace('tgo_', '')}"
        return {
            "entityNme": entity_name,
            "cpNmeUnq": "cp_[adh3_hive]_[dp_dsb]_[]_[]",
            "dsNme": "dl_pk_iar",
            "dsDesc": "Схема «ПК ИАР». БД Hive Блока хранения данных Платформы данных.",
            "detNmeUnq": "table",
            "destNmeUnq": "hive_orc",
            "ddmtNmeUnq": "snapshot",
            "entityDesc": self.description,
            "attributes": {"upsert": attrs},
        }

    def _build_staging_body(self) -> dict:
        attrs = []
        # Бизнес-ключи как PK
        for bk in self.business_keys:
            src_attr = next(a for a in self.source_attrs if a.name == bk)
            hive_type, length, prec = self._map_pg_to_hive(src_attr)
            attrs.append(self._make_attr_dict(
                name=bk, pk_flag=True, typ=hive_type, length=length, prec=prec,
                desc=src_attr.comment, mandatory=True))
        # id_pk_iar
        id_attr = next((a for a in self.source_attrs if a.name == "id"), None)
        if id_attr:
            hive_type, length, prec = self._map_pg_to_hive(id_attr)
            attrs.append(self._make_attr_dict(
                name="id_pk_iar", pk_flag=False, typ=hive_type, length=length, prec=prec,
                desc=id_attr.comment, mandatory=False))
        # Остальные атрибуты (кроме бизнес-ключей и id)
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
        return {
            "entityNme": f"staging_cl_{self.domain}",
            "cpNmeUnq": "cp_[adh3_hive]_[dp_dsb]_[]_[]",
            "dsNme": "dl_iascb_tgo5",
            "dsDesc": "Схема Риски ТГО5. БД Hive Блока хранения данных Платформы данных.",
            "detNmeUnq": "table",
            "destNmeUnq": "hive_orc",
            "ddmtNmeUnq": "snapshotpartition",
            "entityDesc": f"Промежуточный слой для {self.description.lower()}",
            "attributes": {"upsert": attrs},
        }

    def _build_hub_body(self) -> dict:
        attrs = []
        attrs.append(self._make_attr_dict(
            name=self.hub_hashkey_name, pk_flag=True, typ="string", length=0, prec=0,
            desc=f"Хэш-ключ {self.description.lower()}", mandatory=True))
        for bk in self.business_keys:
            src_attr = next(a for a in self.source_attrs if a.name == bk)
            hive_type, length, prec = self._map_pg_to_hive(src_attr)
            attrs.append(self._make_attr_dict(
                name=bk, pk_flag=False, typ=hive_type, length=length, prec=prec,
                desc=src_attr.comment, mandatory=False))
        attrs.append(self._make_attr_dict(
            name="load_date", pk_flag=False, typ="timestamp", length=0, prec=0,
            desc="Дата загрузки", mandatory=False))
        attrs.append(self._make_attr_dict(
            name="record_source", pk_flag=False, typ="string", length=0, prec=0,
            desc="Источник записи", mandatory=False))
        for tech in self._get_tech_fields(with_partition=False):
            attrs.append(self._make_attr_dict(
                name=tech["name"], pk_flag=False, typ=tech["type"], length=0, prec=0,
                desc=tech["desc"], mandatory=True, tech=True))
        return {
            "entityNme": f"domain_{self.domain}_hub",
            "cpNmeUnq": "cp_[adh3_hive]_[dp_dsb]_[]_[]",
            "dsNme": "dl_iascb_tgo5",
            "dsDesc": "Схема Риски ТГО5. БД Hive Блока хранения данных Платформы данных.",
            "detNmeUnq": "table",
            "destNmeUnq": "hive_orc",
            "ddmtNmeUnq": "factwithoutpartition",
            "entityDesc": f"Хаб для {self.description.lower()}",
            "attributes": {"upsert": attrs},
        }

    def _build_sat_body(self) -> dict:
        attrs = []
        attrs.append(self._make_attr_dict(
            name=self.hub_hashkey_name, pk_flag=True, typ="string", length=0, prec=0,
            desc=f"Хэш-ключ {self.description.lower()}", mandatory=True))
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
        return {
            "entityNme": f"domain_{self.domain}_sat",
            "cpNmeUnq": "cp_[adh3_hive]_[dp_dsb]_[]_[]",
            "dsNme": "dl_iascb_tgo5",
            "dsDesc": "Схема Риски ТГО5. БД Hive Блока хранения данных Платформы данных.",
            "detNmeUnq": "table",
            "destNmeUnq": "hive_orc",
            "ddmtNmeUnq": "factwithoutpartition",
            "entityDesc": f"Спутник для {self.description.lower()}",
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
                attrs.append(self._make_attr_dict(
                    name=sa.name, pk_flag=False, typ=sa.hive_type,
                    length=38 if sa.hive_type == 'decimal' else 0,
                    prec=0, desc=sa.comment, mandatory=False))
        for tech in self._get_tech_fields(with_partition=True):
            is_part = tech.get("is_part", False)
            attrs.append(self._make_attr_dict(
                name=tech["name"], pk_flag=False, typ=tech["type"], length=0, prec=0,
                desc=tech["desc"], mandatory=True, tech=True, part=is_part))
        return {
            "entityNme": f"mart_{self.domain}",
            "cpNmeUnq": "cp_[adh3_hive]_[dp_dsb]_[]_[]",
            "dsNme": "dl_iascb_tgo5",
            "dsDesc": "Схема Риски ТГО5. БД Hive Блока хранения данных Платформы данных.",
            "detNmeUnq": "table",
            "destNmeUnq": "hive_orc",
            "ddmtNmeUnq": "snapshotpartition",
            "entityDesc": f"Витрина {self.description.lower()}",
            "attributes": {"upsert": attrs},
        }

    # ------------------ Рендеринг с кавычками и комментариями ------------------
    def _render_yaml_block(self, body: dict, is_source: bool = False) -> str:
        # Рекурсивно оборачиваем все строки в двойные кавычки
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
        # Принудительно оборачиваем значение domain в кавычки
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
                lines[0] = "# createIfNotExists entity <-_-> \n" + lines[0]
        return "\n".join(lines)

    # ------------------ Публичные методы генерации ------------------
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
        self.setMinimumSize(950, 750)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # ---- Исходные данные ----
        source_group = QGroupBox("Перечень параметров исходных данных ПК ИАР")
        source_layout = QVBoxLayout()
        self.source_text = QTextEdit()
        self.source_text.setPlaceholderText("Вставьте текст таблицы (включая заголовок) в формате Confluence...")
        source_layout.addWidget(QLabel("Текст таблицы:"))
        source_layout.addWidget(self.source_text)
        self.source_business_keys = QLineEdit()
        self.source_business_keys.setPlaceholderText("Бизнес-ключи через запятую, например: rus_name, lat_name, oksm")
        source_layout.addWidget(QLabel("Бизнес-ключи (голубое выделение):"))
        source_layout.addWidget(self.source_business_keys)
        source_group.setLayout(source_layout)
        main_layout.addWidget(source_group)

        # ---- Параметры СУР ----
        sur_group = QGroupBox("Перечень параметров формируемых для СУР")
        sur_layout = QVBoxLayout()
        self.sur_text = QTextEdit()
        self.sur_text.setPlaceholderText("Вставьте текст таблицы (включая заголовок) в формате Confluence...")
        sur_layout.addWidget(QLabel("Текст таблицы:"))
        sur_layout.addWidget(self.sur_text)
        self.sur_business_keys = QLineEdit()
        self.sur_business_keys.setPlaceholderText("Бизнес-ключи через запятую (обычно совпадают с исходными)")
        sur_layout.addWidget(QLabel("Бизнес-ключи:"))
        sur_layout.addWidget(self.sur_business_keys)
        sur_group.setLayout(sur_layout)
        main_layout.addWidget(sur_group)

        # ---- Названия объектов ----
        names_group = QGroupBox("Названия объектов Data Vault")
        names_layout = QFormLayout()
        self.source_table_name = QLineEdit("tgo_inokorgs")
        self.hub_name = QLineEdit("domain_inok_hub")
        self.sat_name = QLineEdit("domain_inok_sat")
        self.mart_name = QLineEdit("mart_inok")
        self.domain_name = QLineEdit("inok")
        self.description = QLineEdit("Картотека ИНОК")
        self.source_pk = QLineEdit("id")
        names_layout.addRow("Имя исходной таблицы (source):", self.source_table_name)
        names_layout.addRow("Имя хаба (hub):", self.hub_name)
        names_layout.addRow("Имя спутника (sat):", self.sat_name)
        names_layout.addRow("Имя витрины (mart):", self.mart_name)
        names_layout.addRow("Доменное имя (для staging и ключей):", self.domain_name)
        names_layout.addRow("Описание справочника:", self.description)
        names_layout.addRow("Первичный ключ источника (через запятую):", self.source_pk)
        names_group.setLayout(names_layout)
        main_layout.addWidget(names_group)

        # ---- Кнопки ----
        btn_layout = QHBoxLayout()
        self.generate_btn = QPushButton("Сгенерировать и сохранить...")
        self.generate_btn.clicked.connect(self.generate_and_save)
        self.close_btn = QPushButton("Закрыть")
        self.close_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.generate_btn)
        btn_layout.addWidget(self.close_btn)
        main_layout.addLayout(btn_layout)

        # ---- Лог ----
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        main_layout.addWidget(QLabel("Лог:"))
        main_layout.addWidget(self.log_text)

    def log(self, msg: str, error=False):
        prefix = "[ОШИБКА] " if error else "[INFO] "
        self.log_text.append(prefix + msg)

    def generate_and_save(self):
        source_text = self.source_text.toPlainText().strip()
        sur_text = self.sur_text.toPlainText().strip()
        source_bk = [k.strip() for k in self.source_business_keys.text().split(",") if k.strip()]
        sur_bk = [k.strip() for k in self.sur_business_keys.text().split(",") if k.strip()]

        if not source_text:
            QMessageBox.warning(self, "Ошибка", "Введите текст исходных данных")
            return
        if not sur_text:
            QMessageBox.warning(self, "Ошибка", "Введите текст СУР")
            return
        if not source_bk:
            QMessageBox.warning(self, "Ошибка", "Укажите бизнес-ключи для исходных данных")
            return

        try:
            source_attrs = parse_source_from_confluence(source_text)
            sur_attrs = parse_sur_from_confluence(sur_text)
        except Exception as e:
            self.log(f"Ошибка парсинга: {e}", error=True)
            QMessageBox.critical(self, "Ошибка парсинга", str(e))
            return

        # Устанавливаем бизнес-ключи для СУР
        for sa in sur_attrs:
            sa.is_key = sa.name in sur_bk

        pk_list = [pk.strip() for pk in self.source_pk.text().split(",") if pk.strip()]
        if not pk_list:
            pk_list = ["id"]

        source_table = self.source_table_name.text().strip()
        domain = self.domain_name.text().strip()
        desc = self.description.text().strip()
        if not source_table or not domain:
            QMessageBox.warning(self, "Ошибка", "Укажите имя исходной таблицы и домен")
            return

        try:
            generator = DataVaultYamlGenerator(
                domain_name=domain,
                source_table=source_table,
                source_pk=pk_list,
                business_keys=source_bk,
                source_attrs=source_attrs,
                sur_attrs=sur_attrs,
                description=desc,
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
            self.log(f"YAML файлы успешно сохранены в {folder}")
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