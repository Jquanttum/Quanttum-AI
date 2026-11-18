import queue
import threading

import customtkinter as ctk
from core.memory import Memory
from core.assistant import Assistant
from core.features import FeatureRegistry
from core.fileindex import FileIndex
from core.intent import parse_intent
from core.llm import LLMError, OllamaClient
from core.voice import VoiceError, VoiceService
from core.apps import AppPolicy
from core.policy import FolderPolicy, PermissionsStore, default_user_folders
from core.resource_monitor import get_memory_status
from config import (
    APP_NAME, APP_VERSION, MAX_RAM_WARNING_PERCENT, SETTINGS_PATH,
    PERMISSIONS_PATH, FILE_INDEX_PATH, OLLAMA_HOST, VOSK_MODELS_DIR,
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Spoken text is never sent automatically if it would answer a question the
# assistant asked: a stray sound must not be able to approve an action.
VOICE_NEVER_AUTOSEND = {"confirm_yes", "confirm_no", "confirm_alt", "authorise"}


class QuanttumApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} | {APP_VERSION}")
        self.geometry("900x620")
        self.minsize(680, 480)
        self.memory = Memory()
        self.features = FeatureRegistry(SETTINGS_PATH)
        permissions = PermissionsStore(PERMISSIONS_PATH)
        self.policy = FolderPolicy(defaults=default_user_folders(),
                                   store=permissions)
        self.apps = AppPolicy(permissions)
        self.index = FileIndex(FILE_INDEX_PATH)
        try:
            llm_client = OllamaClient(OLLAMA_HOST)
            llm_problem = ""
        except LLMError as error:         # e.g. a non-local host in config
            llm_client, llm_problem = None, str(error)
        self.voice = VoiceService(self.features, [VOSK_MODELS_DIR])
        self.assistant = Assistant(self.memory, features=self.features,
                                   policy=self.policy, index=self.index,
                                   apps=self.apps, llm_client=llm_client,
                                   voice=self.voice)

        # Messages run on a worker thread so the window never freezes while
        # the model is thinking. The worker only puts events on this queue;
        # the window thread is the only one that touches widgets.
        self.events = queue.Queue()
        self.busy = False
        self.streamed = ""

        # Voice: one listening round at a time, also on its own worker thread.
        self.voice_events = queue.Queue()
        self.voice_cancel = threading.Event()
        self.listening = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header, text="QUANTTUM AI",
            font=ctk.CTkFont(size=22, weight="bold")
        ).grid(row=0, column=0, padx=20, pady=(14, 0), sticky="w")

        ctk.CTkLabel(
            header, text="OFFLINE PERSONAL ASSISTANT",
            text_color="gray"
        ).grid(row=1, column=0, padx=20, pady=(0, 12), sticky="w")

        self.status_label = ctk.CTkLabel(header, text="CORE ONLINE")
        self.status_label.grid(row=0, column=1, rowspan=2, padx=20)

        self.chat = ctk.CTkTextbox(self, wrap="word", state="disabled")
        self.chat.grid(row=1, column=0, padx=16, pady=16, sticky="nsew")

        bottom = ctk.CTkFrame(self)
        bottom.grid(row=2, column=0, padx=16, pady=(0, 8), sticky="ew")
        bottom.grid_columnconfigure(0, weight=1)

        self.entry = ctk.CTkEntry(bottom, placeholder_text="Message Quanttum AI...")
        self.entry.grid(row=0, column=0, padx=(10, 8), pady=10, sticky="ew")
        self.entry.bind("<Return>", self.send_message)

        self.mic_button = ctk.CTkButton(
            bottom, text="Mic", width=70, command=self.toggle_listening)
        self.mic_button.grid(row=0, column=1, padx=(0, 8), pady=10)

        self.send_button = ctk.CTkButton(
            bottom, text="Send", width=90, command=self.send_message)
        self.send_button.grid(row=0, column=2, padx=(0, 10), pady=10)

        self.stop_button = ctk.CTkButton(
            bottom, text="Stop", width=90, fg_color="#B54747",
            hover_color="#8F3535", command=self.stop_everything)
        self.stop_button.grid(row=0, column=3, padx=(0, 10), pady=10)
        self.stop_button.grid_remove()

        self.resource_label = ctk.CTkLabel(self, text="Checking system memory...")
        self.resource_label.grid(row=3, column=0, padx=18, pady=(0, 10), sticky="w")

        self.write_chat("Quanttum AI", "System initialised. Type 'help' to see available commands.")
        if self.features.settings_error:
            self.write_chat("Quanttum AI", self.features.settings_error)
        if self.policy.error:
            self.write_chat("Quanttum AI", self.policy.error)
        if llm_problem:
            self.write_chat("Quanttum AI", llm_problem)
        for role, content, _ in self.memory.recent(20):
            if role == "user":
                self.write_chat("You", content)
            elif role == "assistant":
                self.write_chat("Quanttum AI", content)
        self.update_resource_status()
        self.assistant.start_background_services()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def write_chat(self, speaker, message):
        self.chat.configure(state="normal")
        self.chat.insert("end", f"{speaker}\n{message}\n\n")
        self.chat.see("end")
        self.chat.configure(state="disabled")

    # ---- sending a message and showing the reply -------------------------

    def send_message(self, event=None):
        if self.busy:
            return

        message = self.entry.get().strip()

        if not message:
            return

        self.entry.delete(0, "end")
        self.voice.stop_speaking()
        self.write_chat("You", message)
        self.set_busy(True)
        threading.Thread(target=self._worker, args=(message,),
                         daemon=True, name="quanttum-reply").start()
        self.after(30, self._poll_events)

    def _worker(self, message):
        """Runs on a worker thread. Never touches widgets."""

        try:
            answer = self.assistant.respond(
                message, on_token=lambda piece: self.events.put(("token", piece)))
        except Exception as error:
            answer = f"Something went wrong: {error}"

        self.events.put(("done", answer))

    def _poll_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()

                if kind == "token":
                    self.stream_piece(value)
                else:
                    self.finish_reply(value)
                    return
        except queue.Empty:
            pass

        if self.busy:
            self.after(30, self._poll_events)

    def set_busy(self, busy):
        self.busy = busy
        self.status_label.configure(text="THINKING..." if busy else "CORE ONLINE")
        self.send_button.configure(state="disabled" if busy else "normal")
        self.mic_button.configure(
            state="disabled" if busy and not self.listening else "normal")

        if busy:
            self.stop_button.grid()
        else:
            self.stop_button.grid_remove()

    def stream_piece(self, piece):
        self.chat.configure(state="normal")

        if not self.streamed:
            self.chat.insert("end", "Quanttum AI\n")

        self.streamed += piece
        self.chat.insert("end", piece)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def finish_reply(self, answer):
        shown = self.streamed.strip()

        if not shown:
            self.write_chat("Quanttum AI", answer)
        else:
            self.chat.configure(state="normal")

            if answer.startswith(shown):
                self.chat.insert("end", answer[len(shown):] + "\n\n")
                self.chat.configure(state="disabled")
            else:
                # The reply changed after streaming began (for example the
                # model failed part-way): show the final answer in full.
                self.chat.insert("end", "\n\n")
                self.chat.configure(state="disabled")
                self.write_chat("Quanttum AI", answer)

            self.chat.see("end")

        self.streamed = ""
        self.set_busy(False)
        self.entry.focus()

        if not self.listening and self.features.is_enabled("voice_output"):
            self.voice.speak(answer)

    # ---- voice: press Mic, speak, review the text, press Enter ------------

    def stop_everything(self):
        self.assistant.cancel()
        self.voice.stop_speaking()
        self.voice_cancel.set()

    def toggle_listening(self):
        if self.listening:
            self.voice_cancel.set()
            return

        if self.busy:
            return

        status = self.features.status("voice_input")

        if not status.enabled:
            reason = f": {status.reason}" if status.reason else ""
            self.write_chat(
                "Quanttum AI",
                f"Voice input is {status.state}{reason}. "
                "Say: voice status, or: enable voice input")
            return

        self.voice.stop_speaking()
        self.voice_cancel.clear()
        self.listening = True
        self.mic_button.configure(text="Done")
        self.status_label.configure(text="LISTENING...")
        self.entry.delete(0, "end")
        threading.Thread(target=self._listen_worker, daemon=True,
                         name="quanttum-listen").start()
        self.after(50, self._poll_voice)

    def _listen_worker(self):
        """Worker thread: the microphone is open only while this runs."""

        try:
            result = self.voice.listen(
                on_partial=lambda text: self.voice_events.put(("partial", text)),
                cancel=self.voice_cancel)
            self.voice_events.put(("heard", result))
        except VoiceError as error:
            self.voice_events.put(("error", str(error)))
        except Exception as error:
            self.voice_events.put(("error", f"Voice input failed: {error}"))

    def _poll_voice(self):
        try:
            while True:
                kind, value = self.voice_events.get_nowait()

                if kind == "partial":
                    self.entry.delete(0, "end")
                    self.entry.insert(0, value)
                else:
                    self.finish_listening(kind, value)
                    return
        except queue.Empty:
            pass

        if self.listening:
            self.after(50, self._poll_voice)

    def finish_listening(self, kind, value):
        self.listening = False
        self.mic_button.configure(text="Mic")
        self.status_label.configure(text="CORE ONLINE")

        if kind == "error":
            self.write_chat("Quanttum AI", value)
            return

        text = value.text.strip()
        self.entry.delete(0, "end")

        if value.reason == "cancelled":
            return

        if not text:
            self.write_chat(
                "Quanttum AI",
                "I didn't hear anything. Say: test microphone, to check it.")
            return

        # The transcript goes into the box so you can read and fix it. It is
        # only sent for you when auto-send is on, and never if it is an
        # answer to a question (yes / no / an authorisation phrase).
        self.entry.insert(0, text)
        self.entry.focus()

        if self.features.is_enabled("voice_autosend") and (
                parse_intent(text).name not in VOICE_NEVER_AUTOSEND):
            self.send_message()

    def update_resource_status(self):
        # The toggle is re-checked every cycle, so "enable/disable ram
        # monitor" takes effect immediately, without a restart.
        if not self.features.is_enabled("ram_monitor"):
            self.resource_label.configure(
                text="RAM monitor is off (say: enable ram monitor)",
                text_color="gray")
            self.after(5000, self.update_resource_status)
            return

        status = get_memory_status()
        if status:
            text = (
                f"RAM: {status['percent']}% used | "
                f"{status['available_gb']:.2f} GB available of {status['total_gb']:.2f} GB"
            )
            if status["percent"] >= MAX_RAM_WARNING_PERCENT:
                text += " | WARNING: HIGH MEMORY USAGE"
                self.resource_label.configure(text_color="#E6A23C")
            else:
                self.resource_label.configure(text_color="gray")
            self.resource_label.configure(text=text)
        else:
            self.resource_label.configure(text="RAM monitoring unavailable on this system.")
        self.after(5000, self.update_resource_status)

    def on_close(self):
        self.voice_cancel.set()
        self.assistant.shutdown()
        self.memory.close()
        self.destroy()
