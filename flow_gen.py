import re
import sys

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QLabel, QTextEdit, QLineEdit,
                               QCheckBox, QPushButton, QFileDialog, QMessageBox,
                               QGroupBox, QFormLayout, QSpinBox)
from ruamel.yaml import YAML

yaml_parser = YAML(typ='safe')
yaml_dumper = YAML()
yaml_dumper.indent(mapping=2, sequence=4, offset=2)

class WorkflowGenerator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Генератор YAML потока оркестратора (ruamel.yaml)")
        self.setMinimumSize(900, 800)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        layout.addWidget(QLabel("Введите YAML сущностей Data Vault (можно несколько, разделяя ---):"))
        self.entities_edit = QTextEdit()
        self.entities_edit.setPlaceholderText("Вставьте сюда YAML...")
        layout.addWidget(self.entities_edit)

        params_group = QGroupBox("Параметры workflow")
        form_layout = QFormLayout(params_group)
        self.wf_enabled = QCheckBox("wfEnabledFlg")
        self.wf_enabled.setChecked(False)
        form_layout.addRow("Включить поток:", self.wf_enabled)

        self.wf_desc = QLineEdit("справочника")
        form_layout.addRow("Описание (wfDesc):", self.wf_desc)

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

        btn_layout = QHBoxLayout()
        self.generate_btn = QPushButton("Сгенерировать и сохранить")
        self.close_btn = QPushButton("Закрыть")
        btn_layout.addWidget(self.generate_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self.generate_btn.clicked.connect(self.generate_and_save)
        self.close_btn.clicked.connect(self.close)

    def parse_entities(self, yaml_text):
        """Разбирает многосущностный YAML, поддерживая нестрогое разделение."""
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
                # Пробуем разбить по строкам, начинающимся с "# createIfNotExists entity"
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

    def classify_entities(self, entities):
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
        if is_source:
            return f"{cp}__{ds}__v${name}"
        else:
            return f"{cp}__{ds}__{name}"

    def generate_workflow(self, classified, params):
        if not classified['source']:
            raise ValueError("Не найден источник (source entity)")
        if not classified['raw']:
            raise ValueError("Не найден сырой слой (raw snapshot entity)")
        if not classified['staging']:
            raise ValueError("Не найдена staging сущность")
        if not classified['hub']:
            raise ValueError("Не найдена hub сущность")
        if not classified['sat']:
            raise ValueError("Не найдена sat сущность (обязательна для полного Data Vault)")
        if not classified['mart']:
            raise ValueError("Не найдена mart сущность")

        src = classified['source']
        raw = classified['raw']
        stg = classified['staging']
        hub = classified['hub']
        sat = classified['sat']
        mart = classified['mart']

        src_table = self.build_table_name(src, is_source=True)
        raw_table = self.build_table_name(raw, is_source=False)
        stg_table = self.build_table_name(stg, is_source=False)
        hub_table = self.build_table_name(hub, is_source=False)
        sat_table = self.build_table_name(sat, is_source=False)
        mart_table = self.build_table_name(mart, is_source=False)

        src_name = src['entityNme'].replace('v$', '')
        raw_name = raw['entityNme']
        stg_name = stg['entityNme']
        hub_name = hub['entityNme']
        sat_name = sat['entityNme']
        mart_name = mart['entityNme']

        project_name = mart_name.replace('mart_', '')

        # Формируем имя workflow в строгом соответствии с шаблоном
        src_ds = src.get('dsNme', '')
        raw_ds = raw.get('dsNme', '')
        wf_name = f"wf_[postgresql]_[pk_iar]_[{src_ds}]_2_[adh3_hive]_[ias_kb]_[{raw_ds}]_[{mart_name}]"

        enabled = "true" if params['wf_enabled'] else "false"
        last_run = params['last_run_time']
        tags = [t.strip() for t in params['tags'].split(',') if t.strip()]

        import_task = f"""
      - tskNme: "tsk_import_[{raw['dsNme']}]_[{raw_name}]_[snapshot_full]"
        tskEnabledFlg: true
        tskMandatoryFlg: true
        tskRestartFlg: true
        tskDesc: "Загрузка {params['wf_desc']} в слой {raw['dsNme']}"
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
              paramVal: "{params['app_import']}"
            - paramNme: resource
              paramTyp: PARAMETER_GROUP
              paramVal: "{params['resource']}"
        taskRestartRuleLinks:
          add:
            - {params['restart_rule']}"""

        staging_task = f"""
      - tskNme: "tsk_transform_[{stg['dsNme']}]_[{stg_name}]_[snapshotpartition_full]"
        tskEnabledFlg: true
        tskMandatoryFlg: true
        tskRestartFlg: true
        tskDesc: "Расчёт staging слоя '{params['wf_desc']}'"
        dtltNmeUnq: FULL__APPEND
        grpNmeUnq: grp_spark_common_livy
        parameters:
          upsert:
            - paramNme: task
              paramTyp: NORMAL
              paramVal: "tgo5/{project_name}/hive_{stg['dsNme']}_{stg_name}"
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
              paramVal: "{params['common_app']}"
            - paramNme: application
              paramTyp: PARAMETER_GROUP
              paramVal: "{params['app_transform']}"
            - paramNme: mode
              paramTyp: NORMAL
              paramVal: "append"
        conditions:
          upsert:
            - depTyp: "parentTasks"
              depParents:
                - "tsk_import_[{raw['dsNme']}]_[{raw_name}]_[snapshot_full]"
        taskRestartRuleLinks:
          add:
            - {params['restart_rule']}"""

        hub_task = f"""
      - tskNme: "tsk_transform_[{hub['dsNme']}]_[{hub_name}]_[factwithoutpartition_full]"
        tskEnabledFlg: true
        tskMandatoryFlg: true
        tskRestartFlg: true
        tskDesc: "Расчёт domain hub слоя '{params['wf_desc']}'"
        dtltNmeUnq: FULL__APPEND
        grpNmeUnq: grp_spark_common_livy
        parameters:
          upsert:
            - paramNme: task
              paramTyp: NORMAL
              paramVal: "tgo5/{project_name}/hive_{hub['dsNme']}_{hub_name}"
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
              paramVal: "{params['common_app']}"
            - paramNme: application
              paramTyp: PARAMETER_GROUP
              paramVal: "{params['app_transform']}"
            - paramNme: mode
              paramTyp: NORMAL
              paramVal: "append"
        conditions:
          upsert:
            - depTyp: "parentTasks"
              depParents:
                - "tsk_transform_[{stg['dsNme']}]_[{stg_name}]_[snapshotpartition_full]"
        taskRestartRuleLinks:
          add:
            - {params['restart_rule']}"""

        sat_task = f"""
      - tskNme: "tsk_transform_[{sat['dsNme']}]_[{sat_name}]_[factwithoutpartition_full]"
        tskEnabledFlg: true
        tskMandatoryFlg: true
        tskRestartFlg: true
        tskDesc: "Расчёт domain sat слоя '{params['wf_desc']}'"
        dtltNmeUnq: FULL__APPEND
        grpNmeUnq: grp_spark_common_livy
        parameters:
          upsert:
            - paramNme: task
              paramTyp: NORMAL
              paramVal: "tgo5/{project_name}/hive_{sat['dsNme']}_{sat_name}"
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
              paramVal: "{params['common_app']}"
            - paramNme: application
              paramTyp: PARAMETER_GROUP
              paramVal: "{params['app_transform']}"
            - paramNme: mode
              paramTyp: NORMAL
              paramVal: "append"
        conditions:
          upsert:
            - depTyp: "parentTasks"
              depParents:
                - "tsk_transform_[{hub['dsNme']}]_[{hub_name}]_[factwithoutpartition_full]"
        taskRestartRuleLinks:
          add:
            - {params['restart_rule']}"""

        mart_task = f"""
      - tskNme: "tsk_transform_[{mart['dsNme']}]_[{mart_name}]_[snapshotpartition_full]"
        tskEnabledFlg: true
        tskMandatoryFlg: true
        tskRestartFlg: true
        tskDesc: "Расчёт витрины '{params['wf_desc']}'"
        dtltNmeUnq: FULL__APPEND
        grpNmeUnq: grp_spark_common_livy
        parameters:
          upsert:
            - paramNme: task
              paramTyp: NORMAL
              paramVal: "tgo5/{project_name}/hive_{mart['dsNme']}_{mart_name}"
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
              paramVal: "{params['common_app']}"
            - paramNme: application
              paramTyp: PARAMETER_GROUP
              paramVal: "{params['app_transform']}"
            - paramNme: mode
              paramTyp: NORMAL
              paramVal: "append"
        conditions:
          upsert:
            - depTyp: "parentTasks"
              depParents:
                - "tsk_transform_[{sat['dsNme']}]_[{sat_name}]_[factwithoutpartition_full]"
        taskRestartRuleLinks:
          add:
            - {params['restart_rule']}"""

        tags_yaml = ""
        if tags:
            tags_yaml = "\n    add:\n" + "\n".join(f"      - {t}" for t in tags)

        workflow = f"""# createIfNotExists workflow <-_->
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

  tagLinks:{tags_yaml}

  tasks:
    upsert:{import_task}{staging_task}{hub_task}{sat_task}{mart_task}
"""
        return workflow

    def generate_and_save(self):
        yaml_text = self.entities_edit.toPlainText().strip()
        if not yaml_text:
            QMessageBox.warning(self, "Нет данных", "Введите YAML сущностей.")
            return

        entities = self.parse_entities(yaml_text)
        if not entities:
            return

        # Проверим, что все сущности распознаны и дадим предупреждение о неверном dsNme источника
        classified = self.classify_entities(entities)

        missing = [k for k, v in classified.items() if v is None]
        if missing:
            QMessageBox.warning(self, "Неполные данные", f"Не найдены сущности: {', '.join(missing)}")
            return

        src = classified['source']
        if src.get('dsNme') != 'tgo2':
            reply = QMessageBox.warning(
                self, "Некорректная схема источника",
                f"У сущности источника dsNme = '{src.get('dsNme')}', ожидается 'tgo2'.\n"
                "Продолжить генерацию? (оркестратор может отклонить workflow)",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                return

        params = {
            'wf_enabled': self.wf_enabled.isChecked(),
            'wf_desc': self.wf_desc.text(),
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