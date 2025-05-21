import subprocess
import os
import platform
import tempfile
import json
import logging
import time
import shlex
import threading

logger = logging.getLogger(__name__)


class TUIManager:
    def __init__(self, run_script_path, width=300, height=52):
        self.run_script_path = run_script_path
        self.width = width
        self.height = height
        self.chunk_queue = []
        self.queue_lock = threading.Lock()
        self.tui_active = False
        self.tmpfile_path = None
        self.tui_process = None  # To keep track of the TUI process
        self.processing_thread = None # To keep track of the queue processing thread

    def open_new_terminal(self, data):
        """
        将数据块添加到队列，并确保TUI正在运行以处理队列。
        """
        with self.queue_lock:
            self.chunk_queue.append(data)
            logger.info(f"Added chunk to queue. Queue size: {len(self.chunk_queue)}")

        if not self.tui_active:
            self.tui_active = True
            logger.info("TUI not active. Launching TUI.")

            # Create the temporary file if it doesn't exist
            if not self.tmpfile_path:
                with tempfile.NamedTemporaryFile(
                    mode="w+", delete=False, suffix=".json"
                ) as tmpfile:
                    self.tmpfile_path = tmpfile.name
                    logger.info(f"Temporary file created at: {self.tmpfile_path}")
            
            # Start the TUI process in a new thread
            # The actual TUI launching logic will be moved to a separate method
            # and called here. For now, we'll just log.
            # self._launch_tui_application() # This will be implemented later

            # Start the queue processing thread
            self.processing_thread = threading.Thread(target=self._process_tui_queue)
            self.processing_thread.daemon = True  # Ensure thread exits when main program exits
            self.processing_thread.start()
            logger.info("TUI queue processing thread started.")
            # The TUI launch and initial data write will be handled by _process_tui_queue
            # or a dedicated TUI launch method.
            # For now, we just return, as the TUI is managed by the thread.
            # The method's responsibility is now to enqueue and ensure processor is running.
            return {"status": "queued", "queue_size": len(self.chunk_queue)} # Or some other meaningful response
        else:
            logger.info("TUI already active. Chunk queued.")
            # If TUI is active, _process_tui_queue will pick up the new chunk.
            # We might want to signal the processing thread that new data is available if it's waiting.
            # This can be done using a threading.Event if necessary.
            return {"status": "queued", "queue_size": len(self.chunk_queue)}


    def _launch_tui_application(self, initial_data_chunk):
        """
        实际启动TUI应用程序的逻辑。
         ഇത് open_new_terminal-ൽ നിന്ന് വേർതിരിച്ചെടുത്തു.
        """
        system = platform.system()
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(self.run_script_path), "..", "..")
        )

        tui_input_data = {
            "subtitle_entries": initial_data_chunk,
            "tui_completed": False,
            "merge_map": [],
            "all_chunks_processed": False # New flag
        }

        # Serialize data to the existing temporary file
        with open(self.tmpfile_path, "w", encoding="utf-8") as tmpfile:
            json.dump(tui_input_data, tmpfile, ensure_ascii=False, indent=4)
            logger.info(f"Initial data written to temporary file: {self.tmpfile_path}")

        venv_activate = self.find_virtualenv_activate(project_root)

        command = f"python {self.run_script_path} {self.tmpfile_path}"
        full_command = ""

        if system == "Windows":
            if venv_activate:
                full_command = f'start cmd /c "mode con: cols={self.width} lines={self.height} && cd /d "{project_root}" && "{venv_activate}" && {command} && exit"'
            else:
                full_command = f'start cmd /c "mode con: cols={self.width} lines={self.height} && cd /d "{project_root}" && {command} && exit"'
        elif system == "Darwin":
            if venv_activate:
                apple_script = f"""
                tell application "Terminal"
                    do script "printf '\\\\e[8;{self.height};{self.width}t' && source {shlex.quote(venv_activate)} && cd {shlex.quote(project_root)} && {command}; exit"
                    activate
                end tell
                """
            else:
                apple_script = f"""
                tell application "Terminal"
                    do script "printf '\\\\e[8;{self.height};{self.width}t' && cd {shlex.quote(project_root)} && {command}; exit"
                    activate
                end tell
                """
            full_command = ["osascript", "-e", apple_script]
        elif system == "Linux":
            terminal_command = f"x-terminal-emulator -geometry {self.width}x{self.height}"
            # Note: For Linux, ensuring the terminal closes after the script execution
            # can be tricky and dependent on the specific terminal emulator.
            # The `&& exit` or `; exec bash` then `exit` might not always work as expected
            # if the TUI itself forks or detaches.
            # Using `gnome-terminal --wait` or similar options if available.
            # For simplicity, using a common approach.
            if venv_activate:
                full_command = [
                    "bash",
                    "-c",
                    f"{terminal_command} -e 'bash -c \"source {shlex.quote(venv_activate)} && cd {shlex.quote(project_root)} && {command}; exit\"'",
                ]
            else:
                full_command = [
                    "bash",
                    "-c",
                    f"{terminal_command} -e 'bash -c \"cd {shlex.quote(project_root)} && {command}; exit\"'",
                ]
        else:
            logger.error(f"Unsupported operating system: {system}")
            self.tui_active = False # Reset flag if launch fails
            raise OSError(f"Unsupported operating system: {system}")

        try:
            if system == "Windows":
                self.tui_process = subprocess.Popen(full_command, shell=True)
            elif system == "Darwin":
                 # For macOS, osascript doesn't return a Popen object that we can easily wait on or terminate.
                 # This is a known limitation. We might need other strategies for macOS process management if direct control is needed.
                subprocess.run(full_command, check=True) # This will block until AppleScript is done
                self.tui_process = None # Placeholder, actual process management is tricky
            else: # Linux
                self.tui_process = subprocess.Popen(full_command, shell=False)
            logger.info(f"TUI launched with command: {full_command}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to launch TUI (CalledProcessError): {e}")
            self.tui_active = False
            raise
        except Exception as e:
            logger.error(f"An unexpected error occurred while launching TUI: {e}")
            self.tui_active = False
            raise
    
    def _process_tui_queue(self):
        """
        处理TUI数据队列，并将数据写入临时文件供TUI应用读取。
        """
        first_chunk_processed = False
        while self.tui_active:
            current_chunk = None
            with self.queue_lock:
                if self.chunk_queue:
                    current_chunk = self.chunk_queue.pop(0)
                    logger.info(f"Processing next chunk. Queue size: {len(self.chunk_queue)}")

            if current_chunk:
                if not first_chunk_processed:
                    # Launch TUI with the first chunk
                    try:
                        self._launch_tui_application(current_chunk)
                        first_chunk_processed = True
                    except Exception as e:
                        logger.error(f"Error launching TUI with first chunk: {e}")
                        self.tui_active = False # Stop processing if TUI fails to launch
                        # Re-add chunk to queue? Or handle error appropriately
                        with self.queue_lock:
                            self.chunk_queue.insert(0, current_chunk) # Add back to front
                        break 
                else:
                    # TUI is already running, write new data to the file
                    tui_input_data = {
                        "subtitle_entries": current_chunk,
                        "tui_completed": False,
                        "merge_map": [], # Should merge_map persist or reset? For now, reset.
                        "all_chunks_processed": False
                    }
                    try:
                        with open(self.tmpfile_path, "w", encoding="utf-8") as tmpfile:
                            json.dump(tui_input_data, tmpfile, ensure_ascii=False, indent=4)
                        logger.info(f"New chunk written to {self.tmpfile_path} for TUI.")
                    except Exception as e:
                        logger.error(f"Error writing chunk to TUI file: {e}")
                        # Decide how to handle this error, maybe re-queue and retry?
                        with self.queue_lock:
                            self.chunk_queue.insert(0, current_chunk)
                        time.sleep(1) # Wait a bit before retrying or next cycle
                        continue


                # Wait for TUI to process the current chunk
                while self.tui_active: # Inner loop to wait for tui_completed
                    try:
                        with open(self.tmpfile_path, "r", encoding="utf-8") as f:
                            updated_data = json.load(f)
                        if updated_data.get("tui_completed", False):
                            logger.info(f"TUI completed processing chunk. Merge map: {updated_data.get('merge_map')}")
                            # Potentially save or use merge_map results here
                            break  # Exit inner loop, process next chunk or wait
                        if updated_data.get("all_chunks_processed", False): # TUI signals it's fully done
                            logger.info("TUI signaled all chunks processed or was closed.")
                            self.tui_active = False # This will terminate the outer loop
                            break
                    except json.JSONDecodeError:
                        time.sleep(0.5) # File being written, wait
                    except FileNotFoundError:
                        logger.error("Temporary file not found during TUI processing. Shutting down TUI processing.")
                        self.tui_active = False # Stop processing
                        break # Exit inner loop
                    except Exception as e:
                        logger.error(f"Error reading TUI status file: {e}")
                        time.sleep(1) # Wait before retrying status check
                    time.sleep(0.5) # Polling interval for TUI status

            elif not self.chunk_queue and first_chunk_processed:
                # Queue is empty, and TUI has processed at least one chunk
                # Signal TUI that there are no more chunks for now (unless TUI itself can determine this)
                # Or, if TUI is designed to exit, this loop will end when tui_active becomes false.
                # For now, we assume the TUI will wait or eventually be closed by the user.
                # If the TUI is closed by user, `all_chunks_processed` should be set in the JSON.
                logger.info("Chunk queue is empty. Waiting for new chunks or TUI closure.")
                # Check if TUI is still alive (if possible and implemented)
                # If self.tui_process and self.tui_process.poll() is not None:
                # logger.info("TUI process seems to have terminated.")
                # self.tui_active = False # This will cause the loop to exit.
                # break
                all_chunks_processed_flag = False
                try:
                    with open(self.tmpfile_path, "r", encoding="utf-8") as f:
                        updated_data = json.load(f)
                    if updated_data.get("all_chunks_processed", False):
                        all_chunks_processed_flag = True
                except: # Ignore if file not found or unreadable
                    pass
                
                if all_chunks_processed_flag:
                    logger.info("TUI confirmed all chunks processed. Shutting down queue processor.")
                    self.tui_active = False
                
            time.sleep(1)  # Wait before checking queue again if it was empty

        logger.info("TUI processing queue stopped.")
        self.tui_active = False # Ensure flag is reset
        # Cleanup: Attempt to delete the temporary file if TUIManager is responsible
        if self.tmpfile_path and os.path.exists(self.tmpfile_path):
             try:
                 # Before deleting, write a final state to allow TUI to exit gracefully if it's watching
                final_message = {"all_chunks_processed": True, "tui_completed": True, "subtitle_entries": []}
                with open(self.tmpfile_path, "w", encoding="utf-8") as tmpfile:
                    json.dump(final_message, tmpfile, ensure_ascii=False, indent=4)
                logger.info(f"Signaled TUI to exit and cleaned up {self.tmpfile_path}")
                # Deletion might be problematic if TUI is still accessing it.
                # For now, let's comment out direct deletion from here.
                # os.remove(self.tmpfile_path)
                # self.tmpfile_path = None
             except Exception as e:
                 logger.warning(f"Could not write final message or clean up temp file {self.tmpfile_path}: {e}")
        
        if self.tui_process and self.tui_process.poll() is None: # Check if process is running
            logger.info("Attempting to terminate TUI process.")
            self.tui_process.terminate()
            try:
                self.tui_process.wait(timeout=5) # Wait for termination
            except subprocess.TimeoutExpired:
                logger.warning("TUI process did not terminate in time, killing.")
                self.tui_process.kill()
            self.tui_process = None


    def find_virtualenv_activate(self, project_root):
        """
        查找虚拟环境的激活脚本路径。如果找到，则返回路径；否则返回 None。
        """
        possible_venv_locations = [
            os.path.join(project_root, "venv"),
            os.path.join(project_root, ".venv"),
            os.path.expanduser(
                "~/.virtualenvs/subtitle_llm"
            ),  # For virtualenvwrapper
        ]

        for location in possible_venv_locations:
            if platform.system() == "Windows":
                activate_script = os.path.join(location, "Scripts", "activate.bat")
                activate_ps1 = os.path.join(location, "Scripts", "Activate.ps1") # For PowerShell
                if os.path.exists(activate_script):
                    return activate_script
                elif os.path.exists(activate_ps1):
                    return activate_ps1 # Return PowerShell script if .bat not found
            else: # Linux or macOS
                activate_script = os.path.join(location, "bin", "activate")
                if os.path.exists(activate_script):
                    return activate_script
        logger.warning("Virtual environment activate script not found in common locations.")
        return None

    def get_tui_status(self):
        """
        Returns the current status of the TUI (active or not) and queue size.
        """
        return {"tui_active": self.tui_active, "queue_size": len(self.chunk_queue)}

    def shutdown(self):
        """
        Initiates shutdown of the TUI manager, clearing the queue and stopping the TUI.
        """
        logger.info("TUIManager shutdown initiated.")
        with self.queue_lock:
            self.chunk_queue.clear()
            logger.info("Chunk queue cleared.")

        if self.tui_active:
            self.tui_active = False # This will signal the _process_tui_queue loop to stop
            logger.info("TUI active flag set to False. Processor loop will terminate.")
            # The _process_tui_queue loop handles TUI process termination and temp file cleanup.
        
        # Wait for the processing thread to finish if it's running
        if hasattr(self, 'processing_thread') and self.processing_thread and self.processing_thread.is_alive():
            logger.info("Waiting for TUI processing thread to complete...")
            self.processing_thread.join(timeout=10) # Wait for 10 seconds
            if self.processing_thread.is_alive():
                logger.warning("TUI processing thread did not complete in time.")
        logger.info("TUIManager shutdown process completed.")
