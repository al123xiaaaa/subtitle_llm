import sys
import os
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _ensure_project_root_on_path():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_custom_handling.py <data_file>")
        sys.exit(1)

    _ensure_project_root_on_path()
    from src.models.subtitle_entry import SubtitleEntry
    from src.services.tui.custom_handling import CustomHandlingApp

    data_file = sys.argv[1]

    # 读取传入的 JSON 数据
    with open(data_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    subtitle_entries = [
        SubtitleEntry.from_dict(entry) for entry in data["subtitle_entries"]
    ]

    # 使用传入的 data_file 作为 temp_file_path
    temp_file_path = data_file

    try:
        # 初始化并运行 TUI 应用
        app = CustomHandlingApp(
            subtitle_entries,
            temp_file_path=temp_file_path,
        )
        app.run()

        # 在 TUI 操作完成后，读取更新后的数据
        if os.path.exists(temp_file_path):
            with open(temp_file_path, "r", encoding="utf-8") as f:
                selected_data = json.load(f)
            if selected_data.get("selected_subtitle_entries"):
                print(json.dumps(selected_data, ensure_ascii=False, indent=4))
            else:
                print(
                    json.dumps(
                        {"selected_subtitle_entries": []}, ensure_ascii=False, indent=4
                    )
                )
        else:
            print(
                json.dumps(
                    {"selected_subtitle_entries": []}, ensure_ascii=False, indent=4
                )
            )
    except Exception as e:
        logger.error(f"Error running TUI: {e}")
    finally:
        # 不需要清理 temp_file_path，因为它是传入的
        logger.info(f"TUI handling completed for {temp_file_path}")


if __name__ == "__main__":
    main()
