import customtkinter as ctk
from core.memory import Memory
from core.assistant import Assistant
from core.features import FeatureRegistry
from core.resource_monitor import get_memory_status
from config import APP_NAME, APP_VERSION, MAX_RAM_WARNING_PERCENT, SETTINGS_PATH

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class QuanttumApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} | {APP_VERSION}")
        self.geometry("900x620")
        self.minsize(680, 480)
        self.memory = Memory()
        self.features = FeatureRegistry(SETTINGS_PATH)
        self.assistant = Assistant(self.memory, features=self.features)

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

        ctk.CTkButton(
            bottom, text="Send", width=90, command=self.send_message
        ).grid(row=0, column=1, padx=(0, 10), pady=10)

        self.resource_label = ctk.CTkLabel(self, text="Checking system memory...")
        self.resource_label.grid(row=3, column=0, padx=18, pady=(0, 10), sticky="w")

        self.write_chat("Quanttum AI", "System initialised. Type 'help' to see available commands.")
        if self.features.settings_error:
            self.write_chat("Quanttum AI", self.features.settings_error)
        for role, content, _ in self.memory.recent(20):
            if role == "user":
                self.write_chat("You", content)
            elif role == "assistant":
                self.write_chat("Quanttum AI", content)
        self.update_resource_status()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def write_chat(self, speaker, message):
        self.chat.configure(state="normal")
        self.chat.insert("end", f"{speaker}\n{message}\n\n")
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def send_message(self, event=None):
        message = self.entry.get().strip()
        if not message:
            return
        self.entry.delete(0, "end")
        self.write_chat("You", message)
        response = self.assistant.respond(message)
        self.write_chat("Quanttum AI", response)

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
        self.memory.close()
        self.destroy()
