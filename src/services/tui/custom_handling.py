from textual.app import App, ComposeResult
from textual.widgets import (
    Header,
    Footer,
    DataTable,
    Static,
    Button,
)
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual import events

import logging
import json

from src.models.subtitle_entry import SubtitleEntry

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CustomHandlingApp(App):
    CSS_PATH = "custom_handling.css"
    BINDINGS = [
        # ("q", "quit_chunk_processing", "退出当前块处理"), # Modified 'q' later
        ("space", "select_line", "选择行并翻译"),
        # ("s", "skip_all_retranslation", "跳过所有翻译"), # Modified 's' later
        ("a", "toggle_select", "选择多行"),
        ("m", "merge_selected", "合并选中行"),
    ]

    # Reactive variable to store selected lines
    selected_lines = reactive(set())

    # merge_map to record merge operations
    merge_map = reactive([])
    
    # Flag to indicate if the user wants to finish the entire session
    finish_session_requested = reactive(False)


    # 1. Lifecycle methods
    def __init__(
        self,
        subtitle_entries_data: list[dict], # Now takes list of dicts
        temp_file_path: str,
        **kwargs,
    ):
        super().__init__(**kwargs)
        # Convert dicts to SubtitleEntry objects
        self.subtitle_entries = [SubtitleEntry.from_dict(data) for data in subtitle_entries_data]
        # Assign list_index based on initial order from TUIManager for the current chunk
        for i, entry_obj in enumerate(self.subtitle_entries):
            entry_obj.list_index = i
        self.temp_file_path = temp_file_path
        self.current_merge_map_for_chunk = [] # Store merge map for the current chunk

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(
            Button("Save and Next Chunk", id="save_next", variant="success"),
            Button("Accept All & Next", id="accept_all_next", variant="primary"),
            Button("Finish Session (Close TUI)", id="finish_session", variant="error"),
            classes="button_bar"
        )
        yield Container(
            DataTable(id="subtitles_table"),
            Static(id="status"),
        )
        yield Footer()


    def on_mount(self):
        table = self.query_one("#subtitles_table", DataTable)
        table.add_column("Sel", key="selected", width=5) # Shortened for space
        table.add_column("Idx", key="index", width=5) # Shortened for space
        table.add_column("Original Text", key="original_text", width=60) # Adjusted width
        table.add_column("Translated Text", key="translated_text", width=60) # Adjusted width
        table.add_column("Needs Retrans", key="needs_retranslation", width=15) # Shortened
        table.cursor_type = "row"
        for i, entry in enumerate(self.subtitle_entries):
            needs_retranslation_display = "Yes" if entry.needs_retranslation else "No"
            table.add_row(
                "No",
                f"{entry.index}", # Use actual entry index
                entry.original_text,
                entry.translated_text,
                needs_retranslation_display,
                key=f"row-{entry.index}", # Use actual entry index for key
                height=3, # Increased height for better readability
            )
        if table.row_count > 0:
            table.cursor_coordinate = (0,0) # Set cursor to first row if table is not empty
        self.query_one("#status", Static).update("Chunk loaded. Review or edit subtitles.")


    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save_next":
            await self.action_save_and_next_chunk()
        elif event.button.id == "accept_all_next":
            await self.action_accept_all_and_next_chunk()
        elif event.button.id == "finish_session":
            await self.action_finish_session()

    # 2. Event handling methods (key bindings still useful)
    async def on_key(self, event: events.Key) -> None:
        table = self.query_one("#subtitles_table", DataTable)
        # if event.key == "q": # Replaced by button "Save and Next Chunk"
            # await self.action_save_and_next_chunk()
        if event.key == "space":
            row_key = table.get_row_key(table.cursor_row)
            row_index = self.get_entry_index_from_row_key(row_key) # Helper to get actual entry index
            if row_index is not None: # row_index is now actual entry index
                # Find the SubtitleEntry object by its index
                current_entry = next((e for e in self.subtitle_entries if e.index == row_index), None)
                if current_entry:
                    # Mark this and subsequent entries for retranslation
                    start_retranslating_from = current_entry.list_index # Assuming SubtitleEntry has its list_index
                    
                    self.update_retranslation_status_for_app(0, start_retranslating_from, False)
                    self.update_retranslation_status_for_app(start_retranslating_from, len(self.subtitle_entries), True)
                    
                    self.query_one("#status", Static).update(f"Marked from index {row_index} for re-translation.")
                else:
                    self.query_one("#status", Static).update(f"Error: Could not find entry with index {row_index}.")

        # elif event.key == "s": # Replaced by button "Accept All & Next"
            # await self.action_accept_all_and_next_chunk()
        elif event.key == "a":
            row_key = table.get_row_key(table.cursor_row)
            if row_key:
                self.toggle_selection(row_key)
        elif event.key == "m":
            await self.merge_selected_rows()
        elif event.key == "escape":
            if self.selected_lines:
                self.clear_selection()
                self.query_one("#status", Static).update("Selection cleared.")


    def get_entry_index_from_row_key(self, row_key: str) -> int | None:
        try:
            return int(row_key.split('-')[1])
        except (IndexError, ValueError):
            return None

    def get_entry_by_internal_list_index(self, list_idx: int) -> SubtitleEntry | None:
        if 0 <= list_idx < len(self.subtitle_entries):
            return self.subtitle_entries[list_idx]
        return None

    async def action_save_and_next_chunk(self):
        """User is done with the current chunk, save and prepare for next."""
        # Data to be written is derived from the current state of self.subtitle_entries
        # TUIManager expects a list of dicts
        output_entries = [entry.to_dict() for entry in self.subtitle_entries]
        
        self.write_data_to_temp_file_from_app(output_entries, self.current_merge_map_for_chunk, tui_completed=True, all_chunks_processed=False)
        self.exit() # Exits the current app instance

    async def action_accept_all_and_next_chunk(self):
        """Accept all translations in the current chunk and prepare for next."""
        for entry in self.subtitle_entries:
            entry.needs_retranslation = False
        
        self.update_retranslation_status_in_table_for_all(False)
        self.query_one("#status", Static).update("All translations accepted for this chunk.")
        
        output_entries = [entry.to_dict() for entry in self.subtitle_entries]
        self.write_data_to_temp_file_from_app(output_entries, self.current_merge_map_for_chunk, tui_completed=True, all_chunks_processed=False)
        self.exit()

    async def action_finish_session(self):
        """User wants to finish the entire session."""
        self.finish_session_requested = True # Set flag
        # Write current state, and also signal all_chunks_processed
        output_entries = [entry.to_dict() for entry in self.subtitle_entries]
        self.write_data_to_temp_file_from_app(output_entries, self.current_merge_map_for_chunk, tui_completed=True, all_chunks_processed=True)
        self.exit()

    # 3. Core functionality methods
    def update_retranslation_status_for_app(self, start_list_idx: int, end_list_idx: int, needs_retranslation: bool):
        """Update retranslation status for a range of entries *within the app instance*."""
        table = self.query_one("#subtitles_table", DataTable)
        status_text = "Yes" if needs_retranslation else "No"
        for i in range(start_list_idx, end_list_idx):
            if 0 <= i < len(self.subtitle_entries):
                entry = self.subtitle_entries[i]
                entry.needs_retranslation = needs_retranslation
                row_key = f"row-{entry.index}" # Use actual entry index
                try:
                    table.update_cell(row_key, "needs_retranslation", status_text)
                    # Visual cue can be added here if desired, e.g., by adding/removing a class
                except KeyError:
                    logger.warning(f"Row key {row_key} not found in table for updating retranslation status.")
            else:
                logger.warning(f"List index {i} out of bounds for subtitle_entries while updating retranslation status.")

    def update_retranslation_status_in_table_for_all(self, needs_retranslation: bool):
        """Updates the 'Needs Retranslation' column for all rows in the table."""
        table = self.query_one("#subtitles_table", DataTable)
        status_text = "Yes" if needs_retranslation else "No"
        for i in range(len(self.subtitle_entries)):
            entry = self.subtitle_entries[i]
            row_key = f"row-{entry.index}"
            try:
                table.update_cell(row_key, "needs_retranslation", status_text)
            except KeyError:
                logger.warning(f"Row key {row_key} not found in table during accept all.")


    def toggle_selection(self, row_key: str):
        """Toggles selection for a given row_key."""
        table = self.query_one("#subtitles_table", DataTable)
        entry_index = self.get_entry_index_from_row_key(row_key)
        if entry_index is None: return

        is_selected = row_key in self.selected_lines
        
        # For simplicity, allow multiple disjoint selections for merging.
        # Adjacency check can be enforced in merge_selected_rows if strictly needed.
        if is_selected:
            self.selected_lines.remove(row_key)
            table.remove_class("selected", row_key)
            table.update_cell(row_key, "selected", "No")
            self.query_one("#status", Static).update(f"Deselected row for entry index: {entry_index}")
        else:
            self.selected_lines.add(row_key)
            table.add_class("selected", row_key)
            table.update_cell(row_key, "selected", "Yes")
            self.query_one("#status", Static).update(f"Selected row for entry index: {entry_index}")

    def clear_selection(self):
        """Clears all current selections."""
        table = self.query_one("#subtitles_table", DataTable)
        for row_key in list(self.selected_lines): # Iterate over a copy
            table.remove_class("selected", row_key)
            table.update_cell(row_key, "selected", "No")
        self.selected_lines.clear()
        self.query_one("#status", Static).update("All selections cleared.")


    async def merge_selected_rows(self):
        """Merges selected rows in the TUI."""
        if len(self.selected_lines) < 2:
            self.query_one("#status", Static).update("Need to select at least two rows to merge.")
            return

        # Get SubtitleEntry objects for selected rows, sorted by their original list index
        selected_entries_to_merge = sorted(
            [entry for entry in self.subtitle_entries if f"row-{entry.index}" in self.selected_lines],
            key=lambda e: e.list_index 
        )
        
        # Check for adjacency based on list_index (which reflects current visual order)
        for i in range(len(selected_entries_to_merge) - 1):
            if selected_entries_to_merge[i+1].list_index != selected_entries_to_merge[i].list_index + 1:
                self.query_one("#status", Static).update("Please select adjacent rows (in current display order) to merge.")
                # self.clear_selection() # Optionally clear selection on invalid merge attempt
                return

        # Perform merge operation
        target_entry = selected_entries_to_merge[0]
        merged_original_text = " ".join([e.original_text for e in selected_entries_to_merge])
        # Keep start time of the first, end time of the last
        target_entry.end_time = selected_entries_to_merge[-1].end_time 
        target_entry.original_text = merged_original_text
        target_entry.translated_text = "" # Clear translation as original text changed
        target_entry.needs_retranslation = True

        # Record merge operation for TUIManager
        # These are the original indices from before any merges in this TUI session for this chunk
        self.current_merge_map_for_chunk.append({
            "merged_index": target_entry.index, 
            "merged_from_indices": [e.index for e in selected_entries_to_merge]
        })

        # Remove other merged entries from self.subtitle_entries (iterate backwards)
        indices_to_remove = [e.list_index for e in selected_entries_to_merge[1:]]
        for list_idx in sorted(indices_to_remove, reverse=True):
            del self.subtitle_entries[list_idx]

        # Re-assign list_index for remaining entries
        for i, entry_obj in enumerate(self.subtitle_entries):
            entry_obj.list_index = i
            
        self.rebuild_table_from_internal_state()
        self.clear_selection()
        self.query_one("#status", Static).update(f"Merged {len(selected_entries_to_merge)} entries into entry index {target_entry.index}.")


    def rebuild_table_from_internal_state(self):
        """Rebuilds the table based on the current self.subtitle_entries."""
        table = self.query_one("#subtitles_table", DataTable)
        table.clear(columns=False) # Keep columns, clear rows

        for i, entry in enumerate(self.subtitle_entries):
            entry.list_index = i # Update list_index
            needs_retranslation_display = "Yes" if entry.needs_retranslation else "No"
            table.add_row(
                "No", # Selection status
                f"{entry.index}",
                entry.original_text,
                entry.translated_text,
                needs_retranslation_display,
                key=f"row-{entry.index}",
                height=3,
            )
        if table.row_count > 0:
             table.cursor_coordinate = (0,0)


    # 4. Helper methods
    def write_data_to_temp_file_from_app(self, subtitle_dicts: list, merge_map: list, tui_completed: bool, all_chunks_processed: bool):
        """Helper to write data to the temp file from within the app."""
        data_to_write = {
            "subtitle_entries": subtitle_dicts,
            "merge_map": merge_map,
            "tui_completed": tui_completed,
            "all_chunks_processed": all_chunks_processed, # Include this flag
        }
        try:
            with open(self.temp_file_path, "w", encoding="utf-8") as f:
                json.dump(data_to_write, f, ensure_ascii=False, indent=4)
            logger.info(f"TUI App: Data written to temporary file: {self.temp_file_path}")
        except Exception as e:
            logger.error(f"TUI App: Failed to write data to temporary file: {e}")
            # Optionally, update a status widget in the TUI to inform the user
            self.query_one("#status", Static).update("Error: Could not save changes to file.")

# --- Main script logic outside the App class ---
import sys
import time

def read_json_file(filepath: str) -> dict:
    """Reads data from a JSON file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            logger.info(f"Successfully read data from {filepath}")
            return data
    except FileNotFoundError:
        logger.error(f"File not found: {filepath}. Waiting for TUIManager to create it.")
        # In a loop, we might want to wait and retry, or TUIManager should ensure it exists.
        # For now, returning a default that signals waiting or error.
        return {"tui_completed": True, "all_chunks_processed": False, "subtitle_entries": []} # Default to wait
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from {filepath}. File might be actively written or corrupted.")
        # Return a default that signals waiting or error.
        return {"tui_completed": True, "all_chunks_processed": False, "subtitle_entries": []} # Default to wait
    except Exception as e:
        logger.error(f"An unexpected error occurred while reading {filepath}: {e}")
        return {"tui_completed": True, "all_chunks_processed": False, "subtitle_entries": []} # Default to wait


def write_json_file(filepath: str, data: dict):
    """Writes data to a JSON file."""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logger.info(f"Successfully wrote data to {filepath}")
    except Exception as e:
        logger.error(f"An error occurred while writing to {filepath}: {e}")


def run_tui_for_chunk(subtitle_entries_data: list[dict], tmpfile_path: str) -> tuple[list[dict], list[dict], bool]:
    """
    Initializes and runs the Textual app for the given chunk data.
    Returns the processed subtitle entries (as dicts), the merge map, and a flag indicating if session finish was requested.
    """
    app = CustomHandlingApp(subtitle_entries_data=subtitle_entries_data, temp_file_path=tmpfile_path)
    app.run() # This is blocking until the app exits

    # After app.run() finishes, the app instance (app) holds the final state.
    # The app's exit actions (action_save_and_next_chunk, etc.) should have already written the definitive state to tmpfile_path.
    # So, we re-read tmpfile_path here to get what the TUI considered its final state for the chunk.
    
    final_data_from_tui = read_json_file(tmpfile_path)
    
    processed_entries = final_data_from_tui.get("subtitle_entries", subtitle_entries_data) # Fallback if key missing
    merge_map_from_tui = final_data_from_tui.get("merge_map", [])
    session_finish_requested_by_tui = final_data_from_tui.get("all_chunks_processed", False) # Check if TUI set this

    return processed_entries, merge_map_from_tui, session_finish_requested_by_tui


def main():
    if len(sys.argv) < 2:
        print("Usage: python custom_handling.py <tmpfile_path>")
        sys.exit(1)
    
    tmpfile_path = sys.argv[1]
    logger.info(f"Custom TUI handler started. Watching file: {tmpfile_path}")

    while True:
        logger.info(f"Waiting for new chunk data in {tmpfile_path}...")
        current_data = {}
        # Initial wait for tui_completed to be False (new chunk) or all_chunks_processed
        while True:
            current_data = read_json_file(tmpfile_path)
            if not current_data.get("tui_completed", True) or current_data.get("all_chunks_processed"):
                logger.info("New data or termination signal received.")
                break
            time.sleep(0.5) # Polling interval

        if current_data.get("all_chunks_processed"):
            logger.info("Termination signal (all_chunks_processed) received from TUIManager. Exiting.")
            break

        subtitle_entries_for_tui = current_data.get("subtitle_entries", [])
        if not subtitle_entries_for_tui:
            logger.warning("No subtitle entries found in the current chunk. Waiting for next.")
            # We still need to write tui_completed:True to acknowledge processing this empty/invalid chunk.
            output_data_on_empty = {
                "subtitle_entries": [],
                "merge_map": [],
                "tui_completed": True,
                "all_chunks_processed": False 
            }
            write_json_file(tmpfile_path, output_data_on_empty)
            continue # Go to next iteration of outer loop to wait for new chunk

        logger.info(f"Processing chunk with {len(subtitle_entries_for_tui)} entries.")
        
        # Run the TUI for the current chunk.
        # run_tui_for_chunk will handle app.run() and then read the file that app wrote to.
        processed_subtitle_entries, merge_map, finish_session_requested = run_tui_for_chunk(subtitle_entries_for_tui, tmpfile_path)

        # The TUI app itself (via action_save_and_next_chunk etc.) already writes its state with tui_completed: True.
        # The main concern here is if the user explicitly requested to finish the entire session via a TUI button.
        if finish_session_requested:
            logger.info("User requested to finish session from TUI. Exiting main loop.")
            # The TUI should have already written all_chunks_processed: True.
            # If not, we ensure it here, but ideally, the TUI's "Finish Session" button handles this.
            final_output_data = {
                "subtitle_entries": processed_subtitle_entries,
                "merge_map": merge_map,
                "tui_completed": True,
                "all_chunks_processed": True # Ensure this is set if user exits session
            }
            write_json_file(tmpfile_path, final_output_data)
            break # Exit the main while loop

        # If not finishing session, TUIManager expects tui_completed:True, which run_tui_for_chunk (by way of TUI app) should have ensured.
        # The data for the next polling loop is already in the file, written by the TUI app.
        # We now wait for TUIManager to set tui_completed: False for the *next* chunk.
        logger.info("Chunk processing complete. Waiting for TUIManager to provide next chunk...")
        
        # Inner polling loop (already part of the outer loop's start)
        # This loop waits for TUIManager to either send new data (tui_completed=False) 
        # or signal completion (all_chunks_processed=True).
        # The structure of the main while True loop already handles this polling at its beginning.
    
    logger.info("Custom TUI handler finished.")

if __name__ == "__main__":
    main()
