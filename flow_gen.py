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

        self.wf_desc = QLineEdit("Регламент загрузки из <ПК ИАР> в ОД cправочника < название >")
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
            # Добавляем 'v$' только если имя сущности начинается с 'v$'
            if name.startswith('v$'):
                return f"{cp}__{ds}__{name}"
            else:
                return f"{cp}__{ds}__{name}"
        else:
            return f"{cp}__{ds}__{name}"

    def generate_workflow(self, classified, params):
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

        # ---- шаблон – полная копия вашего эталона с плейсхолдерами ----
        # (все отступы и пустые строки сохранены)
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
            'reference_name': self.ref_name.text().strip(),
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