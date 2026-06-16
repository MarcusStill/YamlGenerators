import base64
import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Any

import requests


def get_config_dir() -> str:
    """
    Определяет папку с конфигурациями (configs/monitoring)
    Работает для:
    - Запуска из Python (src/monitoring/workflow_monitor.py)
    - Запуска из EXE (dist/workflow_monitor.exe)
    """
    if getattr(sys, 'frozen', False):
        # Запуск из EXE
        exe_dir = os.path.dirname(sys.executable)

        possible_paths = [
            os.path.join(exe_dir, 'configs', 'monitoring'),
            os.path.join(os.path.dirname(exe_dir), 'configs', 'monitoring'),
            exe_dir,
        ]
    else:
        # Запуск из Python
        script_dir = os.path.dirname(os.path.abspath(__file__))

        # Пробуем разные пути относительно скрипта
        possible_paths = [
            os.path.join(script_dir, '..', '..', 'configs', 'monitoring'),  # из src/monitoring -> configs/monitoring
            os.path.join(script_dir, '..', 'configs', 'monitoring'),  # из monitoring -> configs/monitoring
            os.path.join(script_dir, 'configs', 'monitoring'),  # если configs рядом со скриптом
            os.path.join(os.path.dirname(script_dir), 'configs', 'monitoring'),  # из src
            script_dir,
        ]

    print("\n🔍 Поиск конфигураций...")
    for path in possible_paths:
        config_path = os.path.abspath(path)
        servers_file = os.path.join(config_path, 'servers.txt')
        if os.path.exists(servers_file):
            print(f"  ✅ Найдены конфиги: {config_path}")
            return config_path
        else:
            print(f"  ❌ Не найдено: {servers_file}")

    # Если не нашли, создаем в папке скрипта
    print(f"\n⚠️ Папка configs/monitoring не найдена!")
    print(f"   Создайте ее и положите файлы: servers.txt, workflows.txt, config.txt")

    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


def get_output_dir() -> str:
    """
    Создает папку для отчетов в reports/reports_YYYYMMDD/
    """
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        # Относительно корня проекта (на 2 уровня выше src/monitoring)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        base_dir = os.path.join(script_dir, '..', '..')  # из src/monitoring -> корень

    reports_root = os.path.join(base_dir, 'reports')
    dirname = os.path.join(reports_root, f"reports_{datetime.now().strftime('%Y%m%d')}")

    if not os.path.exists(dirname):
        os.makedirs(dirname)
        print(f"  📁 Создана папка: {dirname}")

    return dirname


class ConfigLoader:
    """Загрузка конфигурации из файлов"""

    @staticmethod
    def load_servers(filename: str = "servers.txt") -> List[Dict[str, str]]:
        """Загружает список серверов из файла"""
        config_dir = get_config_dir()
        filepath = os.path.join(config_dir, filename)

        servers = []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    parts = line.split(',')
                    if len(parts) >= 3:
                        servers.append({
                            "url": parts[0].strip(),
                            "username": parts[1].strip(),
                            "password": parts[2].strip()
                        })
                    else:
                        print(f"⚠️ Неверный формат в {filename}: {line}")
        except FileNotFoundError:
            print(f"❌ Файл {filepath} не найден!")
            print("   Создайте файл с серверами в формате: URL,логин,пароль")
            print(f"   Ожидаемый путь: {filepath}")
            sys.exit(1)

        if not servers:
            print(f"❌ В файле {filepath} нет ни одного сервера!")
            sys.exit(1)

        return servers

    @staticmethod
    def load_workflows(filename: str = "workflows.txt") -> List[str]:
        """Загружает список потоков из файла"""
        config_dir = get_config_dir()
        filepath = os.path.join(config_dir, filename)

        workflows = []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        workflows.append(line)
        except FileNotFoundError:
            print(f"❌ Файл {filepath} не найден!")
            print("   Создайте файл с потоками (по одному на строку)")
            print(f"   Ожидаемый путь: {filepath}")
            sys.exit(1)

        if not workflows:
            print(f"❌ В файле {filepath} нет ни одного потока!")
            sys.exit(1)

        return workflows

    @staticmethod
    def load_config(filename: str = "config.txt") -> Dict[str, Any]:
        """Загружает настройки из файла"""
        config_dir = get_config_dir()
        filepath = os.path.join(config_dir, filename)

        config = {
            "delay_between_requests": 0.5,
            "delay_between_servers": 2.0,
            "timeout": 30,
            "save_csv": True,
            "max_errors_display": 5
        }

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    if ':' in line:
                        key, value = line.split(':', 1)
                        key = key.strip()
                        value = value.strip()

                        if key in config:
                            if isinstance(config[key], bool):
                                config[key] = value.lower() in ['true', '1', 'yes']
                            elif isinstance(config[key], float):
                                config[key] = float(value)
                            elif isinstance(config[key], int):
                                config[key] = int(float(value))
                            else:
                                config[key] = value
        except FileNotFoundError:
            print("ℹ️ Файл config.txt не найден, используются настройки по умолчанию")

        return config


class WorkflowMonitor:
    """Мониторинг статусов потоков"""

    def __init__(self, server_url: str, username: str, password: str, timeout: int = 30):
        self.server_url = server_url.rstrip('/')
        self.endpoint = "/svc/mdc/workflows"
        self.timeout = timeout
        self.is_available = False

        # Авторизация
        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.9,ru-RU;q=0.8,ru;q=0.7",
            "Authorization": f"Basic {credentials}",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "Origin": self.server_url,
            "Referer": f"{self.server_url}/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

    def safe_status_to_string(self, status: Any) -> str:
        """Безопасное преобразование статуса в строку"""
        if status is None:
            return "DISABLED"
        if isinstance(status, dict):
            for key in ["value", "status", "code", "name", "id"]:
                if key in status and status[key] is not None:
                    return str(status[key])
            return "DICT_STATUS"
        if isinstance(status, list):
            return self.safe_status_to_string(status[0]) if status else "EMPTY_LIST"
        if isinstance(status, bool):
            return "TRUE" if status else "FALSE"
        if isinstance(status, (int, float)):
            return str(status)
        return str(status)

    def test_connection(self) -> bool:
        """Проверяет доступность сервера"""
        try:
            url = f"{self.server_url}:8191{self.endpoint}"
            response = self.session.post(url, json={"limit": 1}, timeout=10)
            self.is_available = response.status_code == 200
            return self.is_available
        except:
            self.is_available = False
            return False

    def check_workflow(self, workflow_name: str) -> Dict[str, Any]:
        """Проверяет статус одного потока"""
        clean_name = workflow_name.strip()
        url = f"{self.server_url}:8191{self.endpoint}"

        payload = {
            "contextSearch": clean_name,
            "direction": "desc",
            "doCount": False,
            "doQuery": True,
            "filter": {
                "domain": {"value": None, "exact": False},
                "wfNmeUnq": {"value": None, "exact": False},
                "wfTyp": {"value": None, "exact": False}
            },
            "limit": 50,
            "order": "wfId",
            "page": 1,
            "showRls": True,
            "showTags": True
        }

        try:
            response = self.session.post(url, json=payload, timeout=self.timeout)

            if response.status_code == 200:
                try:
                    data = response.json()

                    if "workflow" in data and data["workflow"]:
                        for wf in data["workflow"]:
                            if wf.get("wfNmeUnq") == clean_name:
                                return {
                                    "name": wf.get("wfNmeUnq"),
                                    "status": self.safe_status_to_string(wf.get("rlsStatus")),
                                    "start_time": wf.get("rlsStartDt", "N/A"),
                                    "id": wf.get("wfId"),
                                    "description": wf.get("wfDesc", ""),
                                    "enabled": wf.get("wfEnabledFlg", False),
                                    "domain": wf.get("domain", ""),
                                    "found": True
                                }

                        return {"name": clean_name, "found": False, "error": "Не найден"}
                    else:
                        return {"name": clean_name, "found": False, "error": "Нет данных"}

                except json.JSONDecodeError:
                    return {"name": clean_name, "found": False, "error": "Ошибка JSON"}
            else:
                return {"name": clean_name, "found": False, "error": f"HTTP {response.status_code}"}

        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            if "Connection refused" in error_msg:
                error_msg = "Сервер недоступен"
            elif "Timeout" in error_msg:
                error_msg = "Таймаут"
            return {"name": clean_name, "found": False, "error": error_msg[:50]}

    def check_multiple_workflows(self, workflow_names: List[str], delay: float = 0.5) -> Dict[str, Dict]:
        """Проверяет несколько потоков"""
        results = {}
        total = len(workflow_names)

        if not self.test_connection():
            print(f"  ⚠️ Сервер недоступен, пропускаем...")
            for name in workflow_names:
                results[name] = {"name": name, "found": False, "error": "Сервер недоступен"}
            return results

        for idx, name in enumerate(workflow_names, 1):
            print(f"  [{idx}/{total}] {name[:60]}...")
            results[name] = self.check_workflow(name)
            if idx < total:
                time.sleep(delay)

        return results


def safe_status_to_display(status: Any) -> str:
    """
    Безопасно преобразует статус в строку для отображения
    """
    # Сначала преобразуем в строку
    if isinstance(status, dict):
        status_str = "DICT_STATUS"
        for key in ["value", "status", "code", "name", "id"]:
            if key in status and status[key] is not None:
                status_str = str(status[key])
                break
    elif isinstance(status, list):
        status_str = str(status[0]) if status else "EMPTY_LIST"
    elif status is None:
        status_str = "NULL"
    elif isinstance(status, bool):
        status_str = "TRUE" if status else "FALSE"
    elif isinstance(status, (int, float)):
        status_str = str(status)
    else:
        status_str = str(status)

    # Затем преобразуем в читаемый вид
    emoji_map = {
        "CLOSED": "✅ ЗАВЕРШЕН",
        "RUNNING": "🔄 ВЫПОЛНЯЕТСЯ",
        "FAILED": "❌ ОШИБКА",
        "ERROR": "❌ ОШИБКА",
        "WAITING": "⏳ ОЖИДАНИЕ",
        "PAUSED": "⏸️ ПРИОСТАНОВЛЕН",
        "SUCCESS": "✅ УСПЕШНО",
        "DISABLED": "⚪ ОТКЛЮЧЕН",
        "NULL": "⚪ НЕАКТИВЕН",
        "DICT_STATUS": "❓ СЛОВАРЬ",
        "EMPTY_LIST": "❓ ПУСТОЙ СПИСОК"
    }

    return emoji_map.get(status_str, status_str)


def safe_status_to_key(status: Any) -> str:
    """
    Безопасно преобразует статус в строку для использования как ключ словаря
    """
    if isinstance(status, dict):
        for key in ["value", "status", "code", "name", "id"]:
            if key in status and status[key] is not None:
                return str(status[key])
        return "DICT_STATUS"
    elif isinstance(status, list):
        return str(status[0]) if status else "EMPTY_LIST"
    elif status is None:
        return "NULL"
    elif isinstance(status, bool):
        return "TRUE" if status else "FALSE"
    elif isinstance(status, (int, float)):
        return str(status)
    else:
        return str(status)


def print_results_summary(results: Dict[str, Dict], server_url: str, max_errors: int = 5):
    """Выводит сводку результатов"""
    print("\n" + "=" * 100)
    print(f"📊 {server_url}")
    print("=" * 100)

    total = len(results)
    found = sum(1 for r in results.values() if r.get("found", False))
    errors = total - found

    status_icon = "✅" if found > 0 else "❌"
    print(f"{status_icon} Всего: {total} | Найдено: {found} | Ошибок: {errors}")

    if found > 0:
        print("-" * 100)
        print(f"{'№':<4} {'Имя потока':<55} {'Статус':<20} {'Время запуска':<25}")
        print("-" * 100)

        idx = 1
        for name, result in results.items():
            if result.get("found", False):
                status = result.get("status", "UNKNOWN")
                start = result.get("start_time", "N/A")

                status_display = safe_status_to_display(status)

                print(f"{idx:<4} {name[:53]:<55} {status_display:<20} {start:<25}")
                idx += 1

    # Ошибки (показываем только первые max_errors)
    errors_list = [(n, r) for n, r in results.items() if not r.get("found")]
    if errors_list:
        print("\n❌ Ошибки:")
        for name, result in errors_list[:max_errors]:
            error = result.get('error', 'Unknown')
            print(f"  • {name[:50]}: {error}")
        if len(errors_list) > max_errors:
            print(f"  • ... и еще {len(errors_list) - max_errors} ошибок")

    print("=" * 100)

    # Статистика по статусам
    status_count = {}
    for result in results.values():
        if result.get("found"):
            status = result.get("status", "UNKNOWN")
            status_key = safe_status_to_key(status)
            status_count[status_key] = status_count.get(status_key, 0) + 1

    if status_count:
        print("\n📈 Статистика:")
        for status, count in sorted(status_count.items(), key=lambda x: x[1], reverse=True):
            emoji = {
                "CLOSED": "✅",
                "DISABLED": "⚪",
                "RUNNING": "🔄",
                "FAILED": "❌",
                "ERROR": "❌",
                "WAITING": "⏳",
                "NULL": "⚪",
                "SUCCESS": "✅",
                "PAUSED": "⏸️"
            }.get(status, "❓")
            print(f"  {emoji} {status}: {count}")
        print("=" * 100)


def save_results_csv(all_results: Dict, filename: str):
    """Сохраняет результаты в CSV с разделителем ;"""
    try:
        with open(filename, "w", encoding="utf-8-sig") as f:
            f.write("Сервер;Имя потока;Статус;Статус (описание);Время запуска;ID;Описание;Включен;Домен\n")

            for server_url, results in all_results.items():
                for name, result in results.items():
                    if result.get("found"):
                        status = result.get("status", "UNKNOWN")
                        start = result.get("start_time", "N/A")
                        wf_id = result.get("id", "")
                        desc = result.get("description", "").replace(";", ",").replace("\n", " ")
                        enabled = "Да" if result.get("enabled", False) else "Нет"
                        domain = result.get("domain", "")

                        status_desc = {
                            "CLOSED": "Завершен",
                            "RUNNING": "Выполняется",
                            "FAILED": "Ошибка",
                            "ERROR": "Ошибка",
                            "WAITING": "Ожидание",
                            "PAUSED": "Приостановлен",
                            "SUCCESS": "Успешно",
                            "DISABLED": "Отключен",
                            "NULL": "Неактивен"
                        }.get(status, status)

                        f.write(
                            f"{server_url};{name};{status};{status_desc};{start};{wf_id};{desc};{enabled};{domain}\n")
                    else:
                        f.write(f"{server_url};{name};NOT_FOUND;Не найден;N/A;N/A;N/A;Нет;N/A\n")
        print(f"📊 CSV: {filename}")
    except Exception as e:
        print(f"⚠️ Ошибка сохранения CSV: {e}")


def save_results_json(all_results: Dict, filename: str):
    """Сохраняет результаты в JSON"""
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
        print(f"💾 JSON: {filename}")
    except Exception as e:
        print(f"⚠️ Ошибка сохранения JSON: {e}")


def save_results_pivot(all_results: Dict, filename: str):
    """Сохраняет сводную таблицу по статусам"""
    try:
        pivot_data = {}
        for server_url, results in all_results.items():
            server_name = server_url.replace('http://', '').replace('https://', '')
            pivot_data[server_name] = {}

            for name, result in results.items():
                if result.get("found"):
                    status = result.get("status", "UNKNOWN")
                    pivot_data[server_name][status] = pivot_data[server_name].get(status, 0) + 1

        with open(filename, "w", encoding="utf-8-sig") as f:
            f.write("Сервер;CLOSED;RUNNING;FAILED;ERROR;WAITING;PAUSED;SUCCESS;DISABLED;NULL;Всего\n")

            for server, statuses in pivot_data.items():
                total = sum(statuses.values())
                row = [
                    server,
                    statuses.get("CLOSED", 0),
                    statuses.get("RUNNING", 0),
                    statuses.get("FAILED", 0),
                    statuses.get("ERROR", 0),
                    statuses.get("WAITING", 0),
                    statuses.get("PAUSED", 0),
                    statuses.get("SUCCESS", 0),
                    statuses.get("DISABLED", 0),
                    statuses.get("NULL", 0),
                    total
                ]
                f.write(";".join(map(str, row)) + "\n")

        print(f"📊 Сводная таблица: {filename}")
    except Exception as e:
        print(f"⚠️ Ошибка сохранения сводной таблицы: {e}")


def main():
    """Главная функция"""
    config_dir = get_config_dir()
    output_dir = get_output_dir()

    print("\n" + "=" * 100)
    print("🚀 МОНИТОРИНГ СТАТУСОВ ПОТОКОВ")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📁 Конфиги: {config_dir}")
    print(f"📁 Отчеты: {output_dir}")
    print("=" * 100)

    # Загрузка конфигурации
    config = ConfigLoader.load_config()
    servers = ConfigLoader.load_servers()
    workflows = ConfigLoader.load_workflows()

    print(f"\n📋 Серверов: {len(servers)}")
    for server in servers:
        print(f"  • {server['url']}")

    print(f"\n📋 Потоков: {len(workflows)}")
    for wf in workflows[:3]:
        print(f"  • {wf[:60]}")
    if len(workflows) > 3:
        print(f"  • ... и еще {len(workflows) - 3}")

    all_results = {}

    for server_idx, server in enumerate(servers, 1):
        print(f"\n🔌 [{server_idx}/{len(servers)}] {server['url']}")
        print("-" * 80)

        monitor = WorkflowMonitor(
            server_url=server['url'],
            username=server['username'],
            password=server['password'],
            timeout=config.get("timeout", 30)
        )

        results = monitor.check_multiple_workflows(
            workflows,
            delay=config.get("delay_between_requests", 0.5)
        )
        all_results[server['url']] = results

        print_results_summary(
            results,
            server['url'],
            max_errors=config.get("max_errors_display", 5)
        )

        if server_idx < len(servers):
            time.sleep(config.get("delay_between_servers", 2.0))

    # Сохранение результатов
    print("\n💾 Сохранение результатов...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    json_file = os.path.join(output_dir, f"report_{timestamp}.json")
    save_results_json(all_results, json_file)

    if config.get("save_csv", True):
        csv_file = os.path.join(output_dir, f"report_{timestamp}.csv")
        save_results_csv(all_results, csv_file)

        pivot_file = os.path.join(output_dir, f"pivot_{timestamp}.csv")
        save_results_pivot(all_results, pivot_file)

    print("\n" + "=" * 100)
    print("✨ ГОТОВО!")
    print(f"📁 Результаты сохранены в: {output_dir}")
    print("=" * 100)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ Прервано пользователем")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback

        traceback.print_exc()
        input("\nНажмите Enter для выхода...")
        sys.exit(1)