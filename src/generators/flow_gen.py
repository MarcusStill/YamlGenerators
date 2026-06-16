import json
import re
import sys
from pathlib import Path
from typing import Dict, List

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QLabel, QTextEdit, QLineEdit,
                               QCheckBox, QPushButton, QFileDialog, QMessageBox,
                               QGroupBox, QFormLayout, QSpinBox, QTabWidget,
                               QPlainTextEdit)
from ruamel.yaml import YAML

yaml_parser = YAML(typ='safe')
yaml_dumper = YAML()
yaml_dumper.indent(mapping=2, sequence=4, offset=2)


class WorkflowGenerator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Генератор YAML потока оркестратора + кастомный workflow")
        self.setMinimumSize(1000, 900)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # Основное поле для ввода YAML сущностей
        main_layout.addWidget(QLabel("Введите YAML сущностей Data Vault (можно несколько, разделяя ---):"))
        self.entities_edit = QTextEdit()
        self.entities_edit.setPlaceholderText("Вставьте сюда YAML...")
        self.entities_edit.setMinimumHeight(200)
        main_layout.addWidget(self.entities_edit)

        # Вкладки: стандартные настройки и кастомный workflow
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Вкладка с параметрами workflow (стандартная)
        self.params_tab = self._create_params_tab()
        self.tabs.addTab(self.params_tab, "Параметры workflow")

        # Вкладка кастомного workflow
        self.custom_tab = self._create_custom_workflow_tab()
        self.tabs.addTab(self.custom_tab, "Workflow (кастомный)")

        # Кнопки
        btn_layout = QHBoxLayout()
        self.generate_btn = QPushButton("Сгенерировать и сохранить (стандартный)")
        self.generate_custom_btn = QPushButton("Сгенерировать кастомный workflow")
        self.generate_custom_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        self.close_btn = QPushButton("Закрыть")
        btn_layout.addWidget(self.generate_btn)
        btn_layout.addWidget(self.generate_custom_btn)
        btn_layout.addWidget(self.close_btn)
        main_layout.addLayout(btn_layout)

        self.generate_btn.clicked.connect(self.generate_and_save)
        self.generate_custom_btn.clicked.connect(self.generate_custom_workflow_action)
        self.close_btn.clicked.connect(self.close)

    def _create_params_tab(self) -> QWidget:
        """Вкладка с параметрами workflow (стандартная)"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        params_group = QGroupBox("Параметры workflow")
        form_layout = QFormLayout(params_group)
        self.wf_enabled = QCheckBox("wfEnabledFlg")
        self.wf_enabled.setChecked(False)
        form_layout.addRow("Включить поток:", self.wf_enabled)

        self.wf_desc = QLineEdit("Регламент загрузки из <ПК ИАР> в ОД cправочника <название>")
        form_layout.addRow("Описание (wfDesc):", self.wf_desc)

        self.ref_name = QLineEdit("картотека инок")
        form_layout.addRow("Название справочника:", self.ref_name)

        self.last_run_time = QSpinBox()
        self.last_run_time.setRange(0, 10080)
        self.last_run_time.setValue(1440)
        form_layout.addRow("lastRunTime (минуты):", self.last_run_time)

        self.tags = QLineEdit("tgo5")
        form_layout.addRow("Метки (через запятую):", self.tags)

        self.task_launcher = QLineEdit("Hadoop3 YARN Spark")
        form_layout.addRow("task_launcher_type:", self.task_launcher)

        self.app_import = QLineEdit("pg_app_[ias_kb_yarn_spark_db-importer_full_adh3]")
        form_layout.addRow("application (import):", self.app_import)

        self.resource = QLineEdit("pg_resources_jdbc_importer_0_1mln")
        form_layout.addRow("resource:", self.resource)

        self.common_app = QLineEdit("pg_app_[basic_transformer_tos_adh3]")
        form_layout.addRow("common_application (transform):", self.common_app)

        self.app_transform = QLineEdit("pg_app_[ias_kb_tgo5]")
        form_layout.addRow("application (transform):", self.app_transform)

        self.restart_rule = QLineEdit("common_[ias_kb]")
        form_layout.addRow("Правило перезапуска:", self.restart_rule)

        layout.addWidget(params_group)
        layout.addStretch()
        return tab

    def _create_custom_workflow_tab(self) -> QWidget:
        """Вкладка для кастомного workflow через JSON"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.cb_use_custom_workflow = QCheckBox("Использовать кастомный workflow (JSON) - игнорирует стандартную классификацию")
        self.cb_use_custom_workflow.setChecked(False)
        layout.addWidget(self.cb_use_custom_workflow)

        layout.addWidget(QLabel("JSON-конфигурация workflow:"))
        self.custom_workflow_json = QPlainTextEdit()
        self.custom_workflow_json.setPlaceholderText("""[
    {
        "type": "import",
        "source": "mso_r2_analyse_ppao_reestr",
        "target": "tgo2_mso_r2_analyse_ppao_reestr",
        "depends": []
    },
    {
        "type": "transform",
        "source": "tgo2_mso_r2_analyse_ppao_reestr",
        "target": "staging_mso_r2_analyse_ppao_reestr",
        "depends": ["tsk_import_[dl_pk_iar]_[tgo2_mso_r2_analyse_ppao_reestr]_[snapshot_full]"]
    }
]""")
        self.custom_workflow_json.setMinimumHeight(400)
        layout.addWidget(self.custom_workflow_json)

        btn_layout = QHBoxLayout()
        btn_load_example = QPushButton("Загрузить пример JSON")
        btn_load_example.clicked.connect(self._load_custom_workflow_example)
        btn_validate = QPushButton("Проверить JSON")
        btn_validate.clicked.connect(self._validate_custom_workflow_json)
        btn_layout.addWidget(btn_load_example)
        btn_layout.addWidget(btn_validate)
        layout.addLayout(btn_layout)

        layout.addStretch()
        return tab

    def _load_custom_workflow_example(self):
        """Загружает пример JSON для кастомного workflow"""
        example = '''[
    {
        "type": "import",
        "source": "source_table_name",
        "target": "snapshot_table_name",
        "depends": []
    },
    {
        "type": "transform",
        "source": "snapshot_table_name",
        "target": "staging_table_name",
        "depends": ["tsk_import_[dl_pk_iar]_[snapshot_table_name]_[snapshot_full]"]
    },
    {
        "type": "hub",
        "source": "staging_table_name",
        "target": "domain_hub_name",
        "depends": ["tsk_transform_[dl_iascb_tgo5]_[staging_table_name]_[snapshotpartition_full]"]
    },
    {
        "type": "sat",
        "source": "staging_table_name",
        "target": "domain_sat_name",
        "hub": "domain_hub_name",
        "depends": ["tsk_transform_[dl_iascb_tgo5]_[staging_table_name]_[snapshotpartition_full]"]
    },
    {
        "type": "mart",
        "source": ["domain_hub_name", "domain_sat_name"],
        "target": "mart_name",
        "depends": [
            "tsk_transform_[dl_iascb_tgo5]_[domain_hub_name]_[factwithoutpartition_full]",
            "tsk_transform_[dl_iascb_tgo5]_[domain_sat_name]_[factwithoutpartition_full]"
        ]
    }
]'''
        self.custom_workflow_json.setPlainText(example)

    def _validate_custom_workflow_json(self):
        """Проверяет корректность JSON"""
        json_text = self.custom_workflow_json.toPlainText().strip()
        if not json_text:
            QMessageBox.warning(self, "Ошибка", "JSON пуст.")
            return
        try:
            config = json.loads(json_text)
            for idx, task in enumerate(config):
                if 'type' not in task:
                    raise ValueError(f"Задача {idx}: отсутствует поле 'type'")
                t = task['type']
                if t not in ('import', 'transform', 'hub', 'sat', 'mart'):
                    raise ValueError(f"Задача {idx}: неизвестный тип '{t}'")
                if 'target' not in task:
                    raise ValueError(f"Задача {idx}: отсутствует 'target'")
                if t in ('import', 'transform'):
                    if 'source' not in task:
                        raise ValueError(f"Задача {idx}: для типа '{t}' требуется 'source'")
                if t == 'sat' and 'hub' not in task:
                    raise ValueError(f"Задача {idx}: для типа 'sat' требуется 'hub'")
            QMessageBox.information(self, "Успех", "JSON корректен.")
        except json.JSONDecodeError as e:
            QMessageBox.critical(self, "Ошибка JSON", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка валидации", str(e))

    # -------------------- Методы для парсинга сущностей --------------------
    def parse_entities(self, yaml_text):
        """Разбирает многосущностный YAML, возвращает список тел сущностей."""
        docs = []
        parts = re.split(r'\n---\s*\n', yaml_text.strip())
        for part in parts:
            if not part.strip():
                continue
            try:
                data = yaml_parser.load(part)
                if data:
                    if 'body' in data:
                        docs.append(data['body'])
                    else:
                        docs.append(data)
            except Exception:
                sub_parts = re.split(r'\n(?=# createIfNotExists entity)', part)
                for sub in sub_parts:
                    if not sub.strip():
                        continue
                    try:
                        data = yaml_parser.load(sub)
                        if data:
                            if 'body' in data:
                                docs.append(data['body'])
                            else:
                                docs.append(data)
                    except Exception as e2:
                        QMessageBox.warning(self, "Ошибка парсинга", f"Не удалось загрузить блок:\n{e2}")
        return docs

    def parse_entities_dict(self, yaml_text: str) -> Dict[str, dict]:
        """Разбирает YAML и возвращает словарь {entityNme: body}"""
        entities = self.parse_entities(yaml_text)
        result = {}
        for ent in entities:
            name = ent.get('entityNme')
            if name:
                result[name] = ent
        return result

    def classify_entities(self, entities):
        """Классифицирует сущности по типам (для стандартного режима)"""
        classified = {
            'source': None,
            'raw': None,
            'staging': None,
            'hub': None,
            'sat': None,
            'mart': None
        }
        for ent in entities:
            ddmt = ent.get('ddmtNmeUnq', '')
            ds = ent.get('dsNme', '')
            dest = ent.get('destNmeUnq', '')
            name = ent.get('entityNme', '')

            if ddmt == 'source' and dest == 'postgres':
                classified['source'] = ent
            elif ddmt == 'snapshot' and ds == 'dl_pk_iar' and dest == 'hive_orc':
                classified['raw'] = ent
            elif ddmt == 'snapshotpartition' and 'mart_' in name:
                classified['mart'] = ent
            elif ddmt == 'snapshotpartition' and ds == 'dl_iascb_tgo5':
                if not classified['staging']:
                    classified['staging'] = ent
            elif ddmt == 'factwithoutpartition' and name.endswith('_hub'):
                classified['hub'] = ent
            elif ddmt == 'factwithoutpartition' and name.endswith('_sat'):
                classified['sat'] = ent
        return classified

    def build_table_name(self, entity, is_source=False):
        cp = entity.get('cpNmeUnq', '')
        ds = entity.get('dsNme', '')
        name = entity.get('entityNme', '')
        return f"{cp}__{ds}__{name}"

    # -------------------- Методы для кастомного workflow --------------------
    def _make_task_name(self, entity: dict, task_type: str) -> str:
        """Формирует имя задачи по типу"""
        ds = entity.get('dsNme', '')
        name = entity['entityNme']
        dtlt_map = {
            'import': 'snapshot_full',
            'transform': 'snapshotpartition_full',
            'hub': 'factwithoutpartition_full',
            'sat': 'factwithoutpartition_full',
            'mart': 'snapshotpartition_full'
        }
        suffix = dtlt_map.get(task_type, 'snapshot_full')
        if task_type == 'import':
            return f"tsk_import_[{ds}]_[{name}]_[{suffix}]"
        else:
            return f"tsk_transform_[{ds}]_[{name}]_[{suffix}]"

    def _make_parent_condition(self, depends: List[str]) -> str:
        """Формирует блок условий с parentTasks (правильный YAML)"""
        if not depends:
            return ""

        # Одинаковый отступ для всех элементов (32 пробела)
        indent = " " * 32
        parents_lines = []
        for dep in depends:
            parents_lines.append(f"{indent}- \"{dep}\"")
        parents_str = "\n".join(parents_lines)

        return f"""
                conditions:
                  upsert:
                    - depTyp: "parentTasks"
                      depParents:
    {parents_str}"""

    def _build_import_task(self, source_entity: dict, target_entity: dict, params: dict,
                           depends: List[str] = None) -> str:
        src_table = self.build_table_name(source_entity, is_source=True)
        tgt_table = self.build_table_name(target_entity)
        task_name = self._make_task_name(target_entity, 'import')
        parent_condition = self._make_parent_condition(depends)

        return f"""
              - tskNme: "{task_name}"
                tskEnabledFlg: true
                tskMandatoryFlg: true
                tskRestartFlg: true
                tskDesc: "Загрузка {params.get('reference_name', 'справочника')} в слой ПК ИАР ОД"
                dtltNmeUnq: FULL__OVERWRITE
                grpNmeUnq: grp_spark_common_livy
                parameters:
                  upsert:
                    - paramNme: sourceTable
                      paramTyp: ENTITY
                      paramVal: "{src_table}"
                      paramEtlDirectionTyp: SRC
                    - paramNme: targetTable
                      paramTyp: ENTITY
                      paramVal: "{tgt_table}"
                      paramEtlDirectionTyp: TRG
                    - paramNme: application
                      paramTyp: PARAMETER_GROUP
                      paramVal: "{params['app_import']}"
                    - paramNme: resource
                      paramTyp: PARAMETER_GROUP
                      paramVal: "{params['resource']}"
                taskRestartRuleLinks:
                  add:
                    - {params['restart_rule']}{parent_condition}"""

    def _build_transform_task(self, source_entities: List[dict], target_entity: dict, params: dict,
                              depends: List[str] = None) -> str:
        tgt_table = self.build_table_name(target_entity)
        task_name = self._make_task_name(target_entity, 'transform')
        ds = target_entity.get('dsNme', 'dl_iascb_tgo5')
        name = target_entity['entityNme']
        project_name = name.replace('staging_', '').replace('mart_', '')
        task_val = f"tgo5/{project_name}/hive_{ds}_{name}"

        src_params = []
        for src_ent in source_entities:
            param_name = f"tbl_{src_ent['entityNme']}"
            src_params.append(f"""
                    - paramNme: {param_name}
                      paramTyp: TABLE_NAME
                      paramVal: "{self.build_table_name(src_ent)}"
                      paramEtlDirectionTyp: SRC""")
        src_params_str = "".join(src_params)
        parent_condition = self._make_parent_condition(depends)

        return f"""
              - tskNme: "{task_name}"
                tskEnabledFlg: true
                tskMandatoryFlg: true
                tskRestartFlg: true
                tskDesc: "Трансформация {params.get('reference_name', 'справочника')}"
                dtltNmeUnq: FULL__APPEND
                grpNmeUnq: grp_spark_common_livy
                parameters:
                  upsert:
                    - paramNme: task
                      paramTyp: NORMAL
                      paramVal: "{task_val}"
                    - paramNme: targetTable
                      paramTyp: ENTITY
                      paramVal: "{tgt_table}"
                      paramEtlDirectionTyp: TRG{src_params_str}
                    - paramNme: common_application
                      paramTyp: PARAMETER_GROUP
                      paramVal: "{params['common_app']}"
                    - paramNme: application
                      paramTyp: PARAMETER_GROUP
                      paramVal: "{params['app_transform']}"
                    - paramNme: mode
                      paramTyp: NORMAL
                      paramVal: "append"
                taskRestartRuleLinks:
                  add:
                    - {params['restart_rule']}{parent_condition}"""

    def _build_hub_task(self, source_entity: dict, target_entity: dict, params: dict,
                        depends: List[str] = None) -> str:
        src_table = self.build_table_name(source_entity)
        tgt_table = self.build_table_name(target_entity)
        task_name = self._make_task_name(target_entity, 'hub')
        ds = target_entity.get('dsNme', 'dl_iascb_tgo5')
        name = target_entity['entityNme']
        project_name = name.replace('domain_', '').replace('_hub', '')
        task_val = f"tgo5/{project_name}/hive_{ds}_{name}"
        parent_condition = self._make_parent_condition(depends)

        return f"""
              - tskNme: "{task_name}"
                tskEnabledFlg: true
                tskMandatoryFlg: true
                tskRestartFlg: true
                tskDesc: "Расчёт hub слоя {params.get('reference_name', 'справочника')}"
                dtltNmeUnq: FULL__APPEND
                grpNmeUnq: grp_spark_common_livy
                parameters:
                  upsert:
                    - paramNme: task
                      paramTyp: NORMAL
                      paramVal: "{task_val}"
                    - paramNme: targetTable
                      paramTyp: ENTITY
                      paramVal: "{tgt_table}"
                      paramEtlDirectionTyp: TRG
                    - paramNme: tbl_{source_entity['entityNme']}
                      paramTyp: TABLE_NAME
                      paramVal: "{src_table}"
                      paramEtlDirectionTyp: SRC
                    - paramNme: tbl_{target_entity['entityNme']}
                      paramTyp: TABLE_NAME
                      paramVal: "{tgt_table}"
                      paramEtlDirectionTyp: SRC
                    - paramNme: common_application
                      paramTyp: PARAMETER_GROUP
                      paramVal: "{params['common_app']}"
                    - paramNme: application
                      paramTyp: PARAMETER_GROUP
                      paramVal: "{params['app_transform']}"
                    - paramNme: mode
                      paramTyp: NORMAL
                      paramVal: "append"
                taskRestartRuleLinks:
                  add:
                    - {params['restart_rule']}{parent_condition}"""

    def _build_sat_task(self, source_entity: dict, target_entity: dict, hub_entity: dict, params: dict,
                        depends: List[str] = None) -> str:
        src_table = self.build_table_name(source_entity)
        tgt_table = self.build_table_name(target_entity)
        hub_table = self.build_table_name(hub_entity)
        task_name = self._make_task_name(target_entity, 'sat')
        ds = target_entity.get('dsNme', 'dl_iascb_tgo5')
        name = target_entity['entityNme']
        project_name = name.replace('domain_', '').replace('_sat', '')
        task_val = f"tgo5/{project_name}/hive_{ds}_{name}"
        parent_condition = self._make_parent_condition(depends)

        return f"""
              - tskNme: "{task_name}"
                tskEnabledFlg: true
                tskMandatoryFlg: true
                tskRestartFlg: true
                tskDesc: "Расчёт sat слоя {params.get('reference_name', 'справочника')}"
                dtltNmeUnq: FULL__APPEND
                grpNmeUnq: grp_spark_common_livy
                parameters:
                  upsert:
                    - paramNme: task
                      paramTyp: NORMAL
                      paramVal: "{task_val}"
                    - paramNme: targetTable
                      paramTyp: ENTITY
                      paramVal: "{tgt_table}"
                      paramEtlDirectionTyp: TRG
                    - paramNme: tbl_{source_entity['entityNme']}
                      paramTyp: TABLE_NAME
                      paramVal: "{src_table}"
                      paramEtlDirectionTyp: SRC
                    - paramNme: tbl_{hub_entity['entityNme']}
                      paramTyp: TABLE_NAME
                      paramVal: "{hub_table}"
                      paramEtlDirectionTyp: SRC
                    - paramNme: tbl_{target_entity['entityNme']}
                      paramTyp: TABLE_NAME
                      paramVal: "{tgt_table}"
                      paramEtlDirectionTyp: SRC
                    - paramNme: common_application
                      paramTyp: PARAMETER_GROUP
                      paramVal: "{params['common_app']}"
                    - paramNme: application
                      paramTyp: PARAMETER_GROUP
                      paramVal: "{params['app_transform']}"
                    - paramNme: mode
                      paramTyp: NORMAL
                      paramVal: "append"
                taskRestartRuleLinks:
                  add:
                    - {params['restart_rule']}{parent_condition}"""

    def _build_mart_task(self, source_entities: List[dict], target_entity: dict, params: dict,
                         depends: List[str] = None) -> str:
        tgt_table = self.build_table_name(target_entity)
        task_name = self._make_task_name(target_entity, 'mart')
        ds = target_entity.get('dsNme', 'dl_iascb_tgo5')
        name = target_entity['entityNme']
        project_name = name.replace('mart_', '')
        task_val = f"tgo5/{project_name}/hive_{ds}_{name}"

        src_params = []
        for src_ent in source_entities:
            src_params.append(f"""
                    - paramNme: tbl_{src_ent['entityNme']}
                      paramTyp: TABLE_NAME
                      paramVal: "{self.build_table_name(src_ent)}"
                      paramEtlDirectionTyp: SRC""")
        src_params_str = "".join(src_params)
        parent_condition = self._make_parent_condition(depends)

        return f"""
              - tskNme: "{task_name}"
                tskEnabledFlg: true
                tskMandatoryFlg: true
                tskRestartFlg: true
                tskDesc: "Расчёт витрины {params.get('reference_name', 'справочника')}"
                dtltNmeUnq: FULL__APPEND
                grpNmeUnq: grp_spark_common_livy
                parameters:
                  upsert:
                    - paramNme: task
                      paramTyp: NORMAL
                      paramVal: "{task_val}"
                    - paramNme: targetTable
                      paramTyp: ENTITY
                      paramVal: "{tgt_table}"
                      paramEtlDirectionTyp: TRG{src_params_str}
                    - paramNme: common_application
                      paramTyp: PARAMETER_GROUP
                      paramVal: "{params['common_app']}"
                    - paramNme: application
                      paramTyp: PARAMETER_GROUP
                      paramVal: "{params['app_transform']}"
                    - paramNme: mode
                      paramTyp: NORMAL
                      paramVal: "append"
                taskRestartRuleLinks:
                  add:
                    - {params['restart_rule']}{parent_condition}"""

    def _transliterate(self, text: str) -> str:
        """Преобразует кириллицу в латиницу (только строчные буквы) для имени workflow"""
        mapping = {
            'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
            'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
            'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
            'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
            'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
            'А': 'a', 'Б': 'b', 'В': 'v', 'Г': 'g', 'Д': 'd', 'Е': 'e', 'Ё': 'yo',
            'Ж': 'zh', 'З': 'z', 'И': 'i', 'Й': 'y', 'К': 'k', 'Л': 'l', 'М': 'm',
            'Н': 'n', 'О': 'o', 'П': 'p', 'Р': 'r', 'С': 's', 'Т': 't', 'У': 'u',
            'Ф': 'f', 'Х': 'kh', 'Ц': 'ts', 'Ч': 'ch', 'Ш': 'sh', 'Щ': 'shch',
            'Ъ': '', 'Ы': 'y', 'Ь': '', 'Э': 'e', 'Ю': 'yu', 'Я': 'ya'
        }
        result = []
        for ch in text:
            if ch in mapping:
                result.append(mapping[ch])
            elif ch.isalnum() or ch == '_':
                result.append(ch.lower())  # <- преобразуем в нижний регистр
            else:
                result.append('_')
        return ''.join(result)

    def _assemble_workflow_yaml(self, tasks_list: List[str], params: dict) -> str:
        """Собирает финальный YAML workflow"""
        # Имя workflow по шаблону платформы
        # wf_[postgresql]_[pk_iar]_[tgo2]_2_[adh3_hive]_[ias_kb]_[dl_pk_iar]_[mart_xxx]
        wf_name = f"wf_[postgresql]_[pk_iar]_[tgo2]_2_[adh3_hive]_[ias_kb]_[dl_pk_iar]_[{params.get('reference_name_latin', 'spravochnik')}]"

        # Приводим к нижнему регистру
        wf_name = wf_name.lower()

        enabled = "true" if params.get('wf_enabled', False) else "false"
        last_run = str(params.get('last_run_time', 1440))
        tags = [t.strip() for t in params.get('tags', '').split(',') if t.strip()]

        tag_section = ""
        if tags:
            tag_section = "  tagLinks:\n    add:\n" + "\n".join(f"      - {t}" for t in tags) + "\n"

        tasks_yaml = "\n".join(tasks_list)

        return f"""# createIfNotExists workflow <-_->
    domain: "ias_kb"
    body:
      wfNmeUnq: {wf_name}
      wfTyp: DATA
      wfDesc: "{params['wf_desc']}"
      wfEnabledFlg: {enabled}
      parameters:
        upsert:
          - paramNme: task_launcher_type
            paramTyp: NORMAL
            paramVal: "{params['task_launcher']}"
            paramDesc: "Параметр для замены связи с Глобальным параметром"

      conditions:
        upsert:
          - depTyp: "lastRunTime"
            depValue: "{last_run}"

    {tag_section}
      tasks:
        upsert:{tasks_yaml}
    """

    def generate_custom_workflow(self, json_config: str, params: dict) -> str:
        """Генерирует workflow по JSON-конфигурации"""
        yaml_text = self.entities_edit.toPlainText().strip()
        if not yaml_text:
            raise ValueError("Не введён YAML сущностей. Вставьте все сущности Data Vault.")

        entities = self.parse_entities_dict(yaml_text)
        if not entities:
            raise ValueError("Не удалось извлечь ни одной сущности из YAML. Проверьте формат.")

        config = json.loads(json_config)
        tasks_yaml = []

        for task_def in config:
            ttype = task_def['type']
            target_name = task_def['target']
            if target_name not in entities:
                raise KeyError(f"Сущность '{target_name}' не найдена в YAML.")
            target_entity = entities[target_name]
            depends = task_def.get('depends', None)

            if ttype == 'import':
                source_name = task_def['source']
                if source_name not in entities:
                    raise KeyError(f"Сущность source '{source_name}' не найдена.")
                source_entity = entities[source_name]
                tasks_yaml.append(self._build_import_task(source_entity, target_entity, params, depends))

            elif ttype == 'transform':
                source_names = task_def['source']
                if isinstance(source_names, str):
                    source_names = [source_names]
                source_entities = []
                for sn in source_names:
                    if sn not in entities:
                        raise KeyError(f"Сущность source '{sn}' не найдена.")
                    source_entities.append(entities[sn])
                tasks_yaml.append(self._build_transform_task(source_entities, target_entity, params, depends))

            elif ttype == 'hub':
                source_name = task_def['source']
                if source_name not in entities:
                    raise KeyError(f"Сущность source '{source_name}' для hub не найдена.")
                source_entity = entities[source_name]
                tasks_yaml.append(self._build_hub_task(source_entity, target_entity, params, depends))

            elif ttype == 'sat':
                source_name = task_def['source']
                hub_name = task_def['hub']
                if source_name not in entities:
                    raise KeyError(f"Сущность staging '{source_name}' для sat не найдена.")
                if hub_name not in entities:
                    raise KeyError(f"Сущность hub '{hub_name}' для sat не найдена.")
                source_entity = entities[source_name]
                hub_entity = entities[hub_name]
                tasks_yaml.append(self._build_sat_task(source_entity, target_entity, hub_entity, params, depends))

            elif ttype == 'mart':
                source_names = task_def['source']
                if isinstance(source_names, str):
                    source_names = [source_names]
                source_entities = []
                for sn in source_names:
                    if sn not in entities:
                        raise KeyError(f"Сущность source '{sn}' для mart не найдена.")
                    source_entities.append(entities[sn])
                tasks_yaml.append(self._build_mart_task(source_entities, target_entity, params, depends))

            else:
                raise ValueError(f"Неизвестный тип задачи: {ttype}")

        return self._assemble_workflow_yaml(tasks_yaml, params)

    def generate_custom_workflow_action(self):
        """Обработчик кнопки генерации кастомного workflow"""
        # Проверяем, что JSON не пуст
        json_text = self.custom_workflow_json.toPlainText().strip()
        if not json_text:
            QMessageBox.warning(self, "Ошибка", "JSON конфигурация пуста. Заполните её на вкладке 'Workflow (кастомный)'.")
            return

        # Проверяем, что YAML сущностей вставлен
        if not self.entities_edit.toPlainText().strip():
            QMessageBox.warning(self, "Ошибка", "Не введён YAML сущностей. Вставьте все сущности Data Vault в верхнее поле.")
            return

        try:
            params = {
                'wf_enabled': self.wf_enabled.isChecked(),
                'wf_desc': self.wf_desc.text(),
                'reference_name': self.ref_name.text().strip(),
                'reference_name_latin': self._transliterate(self.ref_name.text().strip().replace(' ', '_')),
                'last_run_time': self.last_run_time.value(),
                'tags': self.tags.text(),
                'task_launcher': self.task_launcher.text(),
                'app_import': self.app_import.text(),
                'resource': self.resource.text(),
                'common_app': self.common_app.text(),
                'app_transform': self.app_transform.text(),
                'restart_rule': self.restart_rule.text(),
            }
            workflow_yaml = self.generate_custom_workflow(json_text, params)

            default_name = "custom_workflow.yaml"
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Сохранить workflow", default_name, "YAML files (*.yaml *.yml)"
            )
            if file_path:
                Path(file_path).write_text(workflow_yaml, encoding='utf-8')
                QMessageBox.information(self, "Успех", f"Workflow сохранён:\n{file_path}")
        except json.JSONDecodeError as e:
            QMessageBox.critical(self, "Ошибка JSON", f"Некорректный JSON:\n{str(e)}")
        except KeyError as e:
            QMessageBox.critical(self, "Ошибка", f"Сущность не найдена:\n{str(e)}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка генерации", str(e))

    # -------------------- Стандартный режим --------------------
    def generate_workflow(self, classified, params):
        """Стандартный метод генерации workflow (оставлен без изменений)"""
        # ---- получение данных из classified ----
        src = classified['source']
        raw = classified['raw']
        stg = classified['staging']
        hub = classified['hub']
        sat = classified['sat']
        mart = classified['mart']

        # имена таблиц
        src_table = self.build_table_name(src, is_source=True)
        raw_table = self.build_table_name(raw, is_source=False)
        stg_table = self.build_table_name(stg, is_source=False)
        hub_table = self.build_table_name(hub, is_source=False)
        sat_table = self.build_table_name(sat, is_source=False)
        mart_table = self.build_table_name(mart, is_source=False)

        raw_name = raw['entityNme']
        stg_name = stg['entityNme']
        hub_name = hub['entityNme']
        sat_name = sat['entityNme']
        mart_name = mart['entityNme']

        project_name = mart_name.replace('mart_', '')
        src_ds = src.get('dsNme', '')
        raw_ds = raw.get('dsNme', '')
        stg_ds = stg.get('dsNme', '')
        hub_ds = hub.get('dsNme', '')
        sat_ds = sat.get('dsNme', '')
        mart_ds = mart.get('dsNme', '')

        wf_name = f"wf_[postgresql]_[pk_iar]_[{src_ds}]_2_[adh3_hive]_[ias_kb]_[{raw_ds}]_[{mart_name}]"
        enabled = "true" if params['wf_enabled'] else "false"
        last_run = str(params['last_run_time'])
        tags = [t.strip() for t in params['tags'].split(',') if t.strip()]
        ref_name = params.get('reference_name', 'справочника')

        import_desc = f"Загрузка справочника {ref_name} в слой ПК ИАР ОД"
        staging_desc = f"Расчёт staging слоя витрины 'Справочник {ref_name}' ТГО.СУР"
        hub_desc = f"Расчёт domain hub слоя витрины 'Справочник {ref_name}' ТГО.СУР"
        sat_desc = f"Расчёт domain sat слоя витрины 'Справочник {ref_name}' ТГО.СУР"
        mart_desc = f"Расчёт витрины 'Справочник {ref_name}'"

        import_tsk = f"tsk_import_[{raw_ds}]_[{raw_name}]_[snapshot_full]"
        staging_tsk = f"tsk_transform_[{stg_ds}]_[{stg_name}]_[snapshotpartition_full]"
        hub_tsk = f"tsk_transform_[{hub_ds}]_[{hub_name}]_[factwithoutpartition_full]"
        sat_tsk = f"tsk_transform_[{sat_ds}]_[{sat_name}]_[factwithoutpartition_full]"
        mart_tsk = f"tsk_transform_[{mart_ds}]_[{mart_name}]_[snapshotpartition_full]"

        staging_task_val = f"tgo5/{project_name}/hive_{stg_ds}_{stg_name}"
        hub_task_val = f"tgo5/{project_name}/hive_{hub_ds}_{hub_name}"
        sat_task_val = f"tgo5/{project_name}/hive_{sat_ds}_{sat_name}"
        mart_task_val = f"tgo5/{project_name}/hive_{mart_ds}_{mart_name}"

        task_launcher = params['task_launcher']
        app_import = params['app_import']
        resource = params['resource']
        common_app = params['common_app']
        app_transform = params['app_transform']
        restart_rule = params['restart_rule']

        # ---- теги ----
        if tags:
            tag_section = "  tagLinks:\n    add:\n" + "\n".join(f"      - {t}" for t in tags)
        else:
            tag_section = ""

        yaml_template = f'''# createIfNotExists workflow <-_->
    domain: "ias_kb"
    body:
      wfNmeUnq: {wf_name}
      wfTyp: DATA
      wfDesc: "{params['wf_desc']}"
      wfEnabledFlg: {enabled}
      parameters:
        upsert:
          - paramNme: task_launcher_type
            paramTyp: NORMAL
            paramVal: "{task_launcher}"
            paramDesc: "Параметр для замены связи с Глобальным параметром"

      conditions:
        upsert:
          - depTyp: "lastRunTime"
            depValue: "{last_run}"

    {tag_section}
      tasks:
        upsert:
          - tskNme: "{import_tsk}"
            tskEnabledFlg: true
            tskMandatoryFlg: true
            tskRestartFlg: true
            tskDesc: "{import_desc}"
            dtltNmeUnq: FULL__OVERWRITE
            grpNmeUnq: grp_spark_common_livy
            parameters:
              upsert:
                - paramNme: sourceTable
                  paramTyp: ENTITY
                  paramVal: "{src_table}"
                  paramEtlDirectionTyp: SRC
                - paramNme: targetTable
                  paramTyp: ENTITY
                  paramVal: "{raw_table}"
                  paramEtlDirectionTyp: TRG
                - paramNme: application
                  paramTyp: PARAMETER_GROUP
                  paramVal: "{app_import}"
                - paramNme: resource
                  paramTyp: PARAMETER_GROUP
                  paramVal: "{resource}"
            taskRestartRuleLinks:
              add:
                - {restart_rule}

          - tskNme: "{staging_tsk}"
            tskEnabledFlg: true
            tskMandatoryFlg: true
            tskRestartFlg: true
            tskDesc: "{staging_desc}"
            dtltNmeUnq: FULL__APPEND
            grpNmeUnq: grp_spark_common_livy
            parameters:
              upsert:
                - paramNme: task
                  paramTyp: NORMAL
                  paramVal: "{staging_task_val}"
                - paramNme: targetTable
                  paramTyp: ENTITY
                  paramVal: "{stg_table}"
                  paramEtlDirectionTyp: TRG
                - paramNme: tbl_{raw_name}
                  paramTyp: TABLE_NAME
                  paramVal: "{raw_table}"
                  paramEtlDirectionTyp: SRC
                - paramNme: common_application
                  paramTyp: PARAMETER_GROUP
                  paramVal: "{common_app}"
                - paramNme: application
                  paramTyp: PARAMETER_GROUP
                  paramVal: "{app_transform}"
                - paramNme: mode
                  paramTyp: NORMAL
                  paramVal: "append"
            conditions:
              upsert:
                - depTyp: "parentTasks"
                  depParents:
                    - "{import_tsk}"
            taskRestartRuleLinks:
              add:
                - {restart_rule}

          - tskNme: "{hub_tsk}"
            tskEnabledFlg: true
            tskMandatoryFlg: true
            tskRestartFlg: true
            tskDesc: "{hub_desc}"
            dtltNmeUnq: FULL__APPEND
            grpNmeUnq: grp_spark_common_livy
            parameters:
              upsert:
                - paramNme: task
                  paramTyp: NORMAL
                  paramVal: "{hub_task_val}"
                - paramNme: targetTable
                  paramTyp: ENTITY
                  paramVal: "{hub_table}"
                  paramEtlDirectionTyp: TRG
                - paramNme: tbl_{stg_name}
                  paramTyp: TABLE_NAME
                  paramVal: "{stg_table}"
                  paramEtlDirectionTyp: SRC
                - paramNme: tbl_{hub_name}
                  paramTyp: TABLE_NAME
                  paramVal: "{hub_table}"
                  paramEtlDirectionTyp: SRC
                - paramNme: common_application
                  paramTyp: PARAMETER_GROUP
                  paramVal: "{common_app}"
                - paramNme: application
                  paramTyp: PARAMETER_GROUP
                  paramVal: "{app_transform}"
                - paramNme: mode
                  paramTyp: NORMAL
                  paramVal: "append"
            conditions:
              upsert:
                - depTyp: "parentTasks"
                  depParents:
                    - "{staging_tsk}"
            taskRestartRuleLinks:
              add:
                - {restart_rule}

          - tskNme: "{sat_tsk}"
            tskEnabledFlg: true
            tskMandatoryFlg: true
            tskRestartFlg: true
            tskDesc: "{sat_desc}"
            dtltNmeUnq: FULL__APPEND
            grpNmeUnq: grp_spark_common_livy
            parameters:
              upsert:
                - paramNme: task
                  paramTyp: NORMAL
                  paramVal: "{sat_task_val}"
                - paramNme: targetTable
                  paramTyp: ENTITY
                  paramVal: "{sat_table}"
                  paramEtlDirectionTyp: TRG
                - paramNme: tbl_{stg_name}
                  paramTyp: TABLE_NAME
                  paramVal: "{stg_table}"
                  paramEtlDirectionTyp: SRC
                - paramNme: tbl_{hub_name}
                  paramTyp: TABLE_NAME
                  paramVal: "{hub_table}"
                  paramEtlDirectionTyp: SRC
                - paramNme: tbl_{sat_name}
                  paramTyp: TABLE_NAME
                  paramVal: "{sat_table}"
                  paramEtlDirectionTyp: SRC
                - paramNme: common_application
                  paramTyp: PARAMETER_GROUP
                  paramVal: "{common_app}"
                - paramNme: application
                  paramTyp: PARAMETER_GROUP
                  paramVal: "{app_transform}"
                - paramNme: mode
                  paramTyp: NORMAL
                  paramVal: "append"
            conditions:
              upsert:
                - depTyp: "parentTasks"
                  depParents:
                    - "{hub_tsk}"
            taskRestartRuleLinks:
              add:
                - {restart_rule}

          - tskNme: "{mart_tsk}"
            tskEnabledFlg: true
            tskMandatoryFlg: true
            tskRestartFlg: true
            tskDesc: "{mart_desc}"
            dtltNmeUnq: FULL__APPEND
            grpNmeUnq: grp_spark_common_livy
            parameters:
              upsert:
                - paramNme: task
                  paramTyp: NORMAL
                  paramVal: "{mart_task_val}"
                - paramNme: targetTable
                  paramTyp: ENTITY
                  paramVal: "{mart_table}"
                  paramEtlDirectionTyp: TRG
                - paramNme: tbl_{hub_name}
                  paramTyp: TABLE_NAME
                  paramVal: "{hub_table}"
                  paramEtlDirectionTyp: SRC
                - paramNme: tbl_{sat_name}
                  paramTyp: TABLE_NAME
                  paramVal: "{sat_table}"
                  paramEtlDirectionTyp: SRC
                - paramNme: common_application
                  paramTyp: PARAMETER_GROUP
                  paramVal: "{common_app}"
                - paramNme: application
                  paramTyp: PARAMETER_GROUP
                  paramVal: "{app_transform}"
                - paramNme: mode
                  paramTyp: NORMAL
                  paramVal: "append"
            conditions:
              upsert:
                - depTyp: "parentTasks"
                  depParents:
                    - "{sat_tsk}"
            taskRestartRuleLinks:
              add:
                - {restart_rule}'''
        return yaml_template

    def generate_and_save(self):
        """Стандартный метод генерации (требует наличия source, raw, staging, hub, sat, mart)"""
        yaml_text = self.entities_edit.toPlainText().strip()
        if not yaml_text:
            QMessageBox.warning(self, "Нет данных", "Введите YAML сущностей.")
            return

        entities = self.parse_entities(yaml_text)
        if not entities:
            return

        classified = self.classify_entities(entities)

        missing = [k for k, v in classified.items() if v is None]
        if missing:
            QMessageBox.warning(self, "Неполные данные", f"Не найдены сущности: {', '.join(missing)}")
            return

        params = {
            'wf_enabled': self.wf_enabled.isChecked(),
            'wf_desc': self.wf_desc.text(),
            'reference_name': self.ref_name.text().strip(),
            'last_run_time': self.last_run_time.value(),
            'tags': self.tags.text(),
            'task_launcher': self.task_launcher.text(),
            'app_import': self.app_import.text(),
            'resource': self.resource.text(),
            'common_app': self.common_app.text(),
            'app_transform': self.app_transform.text(),
            'restart_rule': self.restart_rule.text(),
        }

        try:
            workflow_yaml = self.generate_workflow(classified, params)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка генерации", str(e))
            return

        default_name = f"{classified['mart']['entityNme']}_workflow.yaml"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить workflow", default_name, "YAML files (*.yaml *.yml)"
        )
        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(workflow_yaml)
            QMessageBox.information(self, "Успех", f"Файл сохранён:\n{file_path}")

def main():
    app = QApplication(sys.argv)
    window = WorkflowGenerator()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()