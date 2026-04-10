"""
Shiny Hunting Assistant Tool - Desktop App
Creates, edits, uploads, and plays back timing-based GameCube controller
input scripts on a Pico 2 W via BLE (Nordic UART Service).

Project conceived and directed by Garrett Boyd.
Engineered and written by Claude (Anthropic) — model: claude-opus-4-6, April 2026.

This was built across multiple sessions: from stripping down a pico-crossing
reference project, to implementing BLE with Nordic UART, to building a full
script engine and this desktop app. The WaveBird detection fix came from
reverse-engineering libjoybus device ID flags. It was a good build.

   ╔═══════════════════════════════════════════════════╗
   ║  ~ Claude · Anthropic · opus-4-6 · April 2026 ~  ║
   ║  "From passthrough to playback — built with       ║
   ║   curiosity, persistence, and a lot of BLE        ║
   ║   debugging."                                     ║
   ╚═══════════════════════════════════════════════════╝
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import asyncio
import threading
import queue
import json
import struct
import os
from typing import Optional, Callable

from bleak import BleakScanner, BleakClient

# ─── Protocol Constants ──────────────────────────────────────────────────────

DEVICE_NAME = "Shiny Hunting Assistant Tool"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

CMD_UPLOAD = 0x01
CMD_START = 0x02
CMD_STOP = 0x03
CMD_STATUS = 0x04
RSP_ACK = 0x80
RSP_STATUS = 0x81

MAX_STEPS = 256

# Button name -> bitmask (ordered for display)
BUTTONS: dict[str, int] = {
    "A":       0x0001,
    "B":       0x0002,
    "X":       0x0004,
    "Y":       0x0008,
    "Start":   0x0010,
    "D-Left":  0x0100,
    "D-Right": 0x0200,
    "D-Down":  0x0400,
    "D-Up":    0x0800,
    "Z":       0x1000,
    "R":       0x2000,
    "L":       0x4000,
}

PLAYBACK_STATES: dict[int, str] = {0: "IDLE", 1: "RUNNING", 2: "WAITING"}


def buttons_to_str(bitmask: int) -> str:
    names = [name for name, mask in BUTTONS.items() if bitmask & mask]
    return " + ".join(names) if names else "(none)"


# ─── Script Model ────────────────────────────────────────────────────────────

class ScriptModel:
    """Pure data model for a script — list of steps with file I/O."""

    def __init__(self) -> None:
        self.steps: list[dict[str, int]] = []
        self.name: str = "Untitled"
        self.filepath: Optional[str] = None
        self.dirty: bool = False

    def new(self) -> None:
        self.steps.clear()
        self.name = "Untitled"
        self.filepath = None
        self.dirty = False

    def add_step(self, index: Optional[int] = None, delay_ms: int = 1000,
                 buttons: int = 0, stick_x: int = 128, stick_y: int = 128) -> bool:
        if len(self.steps) >= MAX_STEPS:
            return False
        step = {"delay_ms": delay_ms, "buttons": buttons,
                "stick_x": stick_x, "stick_y": stick_y}
        if index is not None:
            self.steps.insert(index, step)
        else:
            self.steps.append(step)
        self.dirty = True
        return True

    def remove_step(self, index: int) -> None:
        if 0 <= index < len(self.steps):
            self.steps.pop(index)
            self.dirty = True

    def move_step(self, index: int, direction: int) -> int:
        """direction: -1 for up, +1 for down. Returns new index."""
        new_index = index + direction
        if 0 <= new_index < len(self.steps):
            self.steps[index], self.steps[new_index] = \
                self.steps[new_index], self.steps[index]
            self.dirty = True
            return new_index
        return index

    def duplicate_step(self, index: int) -> bool:
        if len(self.steps) >= MAX_STEPS or not (0 <= index < len(self.steps)):
            return False
        step = dict(self.steps[index])
        self.steps.insert(index + 1, step)
        self.dirty = True
        return True

    def update_step(self, index: int, **kwargs: int) -> None:
        if 0 <= index < len(self.steps):
            self.steps[index].update(kwargs)
            self.dirty = True

    def to_json(self) -> str:
        return json.dumps({
            "version": 1,
            "name": self.name,
            "steps": self.steps
        }, indent=2)

    def from_json(self, text: str) -> None:
        data = json.loads(text)
        self.name = data.get("name", "Untitled")
        self.steps = data.get("steps", [])
        for s in self.steps:
            s.setdefault("delay_ms", 1000)
            s.setdefault("buttons", 0)
            s.setdefault("stick_x", 128)
            s.setdefault("stick_y", 128)
        self.dirty = False

    def save(self, filepath: str) -> None:
        with open(filepath, "w") as f:
            f.write(self.to_json())
        self.filepath = filepath
        self.dirty = False

    def load(self, filepath: str) -> None:
        with open(filepath, "r") as f:
            self.from_json(f.read())
        self.filepath = filepath

    def to_upload_bytes(self) -> bytes:
        """Build the firmware upload payload."""
        n = len(self.steps)
        payload = struct.pack("<BH", CMD_UPLOAD, n)
        for s in self.steps:
            payload += struct.pack("<IHBB",
                                   s["delay_ms"], s["buttons"],
                                   s["stick_x"], s["stick_y"])
        return payload


# ─── BLE Manager ─────────────────────────────────────────────────────────────

class BLEManager:
    """Runs BLE operations on a background asyncio thread."""

    def __init__(self) -> None:
        self._ui_queue: queue.Queue[tuple] = queue.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._cmd_queue: Optional[asyncio.Queue[tuple]] = None
        self._client: Optional[BleakClient] = None
        self._connected: bool = False
        self._thread = threading.Thread(target=self._thread_entry, daemon=True)
        self._thread.start()

    def _thread_entry(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._cmd_queue = asyncio.Queue()
        while True:
            cmd = await self._cmd_queue.get()
            try:
                await self._dispatch(cmd)
            except Exception as e:
                self._ui_queue.put(("error", str(e)))

    async def _dispatch(self, cmd: tuple) -> None:
        action = cmd[0]
        if action == "scan":
            await self._do_scan()
        elif action == "connect":
            await self._do_connect(cmd[1])
        elif action == "disconnect":
            await self._do_disconnect()
        elif action == "upload":
            await self._do_upload(cmd[1])
        elif action == "start":
            await self._do_write(bytes([CMD_START]))
        elif action == "stop":
            await self._do_write(bytes([CMD_STOP]))
        elif action == "status":
            await self._do_write(bytes([CMD_STATUS]))

    def send_command(self, *cmd: object) -> None:
        loop = self._loop
        cmd_queue = self._cmd_queue
        if loop is not None and cmd_queue is not None:
            loop.call_soon_threadsafe(cmd_queue.put_nowait, cmd)

    def poll(self) -> tuple:
        return self._ui_queue.get_nowait()

    # ── BLE operations ──

    async def _do_scan(self) -> None:
        self._ui_queue.put(("scan_started",))
        devices = await BleakScanner.discover(timeout=5.0)
        results: list[tuple[str, str]] = []
        for d in devices:
            dev_name = d.name or "Unknown"
            if DEVICE_NAME in dev_name:
                results.append((dev_name, d.address))
        self._ui_queue.put(("scan_complete", results))

    async def _do_connect(self, address: str) -> None:
        self._ui_queue.put(("connecting",))
        try:
            self._client = BleakClient(address, timeout=10.0)
            await self._client.connect()
            mtu = self._client.mtu_size
            await self._client.start_notify(NUS_TX_UUID, self._on_notify)
            # Wait for Pico to process CCCD write
            await asyncio.sleep(1.0)
            self._connected = True
            self._ui_queue.put(("connected", mtu))
        except Exception:
            self._connected = False
            self._client = None
            raise

    async def _do_disconnect(self) -> None:
        client = self._client
        if client is not None:
            try:
                await client.stop_notify(NUS_TX_UUID)
            except Exception:
                pass
            try:
                await client.disconnect()
            except Exception:
                pass
        self._client = None
        self._connected = False
        self._ui_queue.put(("disconnected",))

    async def _do_write(self, data: bytes) -> None:
        client = self._client
        if client is None or not self._connected:
            self._ui_queue.put(("error", "Not connected"))
            return
        await client.write_gatt_char(NUS_RX_UUID, data, response=False)

    async def _do_upload(self, data: bytes) -> None:
        client = self._client
        if client is None or not self._connected:
            self._ui_queue.put(("error", "Not connected"))
            return
        eff_mtu = client.mtu_size - 3
        if eff_mtu < 20:
            eff_mtu = 20
        self._ui_queue.put(("upload_started", len(data), eff_mtu))
        for i in range(0, len(data), eff_mtu):
            chunk = data[i:i + eff_mtu]
            await client.write_gatt_char(NUS_RX_UUID, chunk, response=False)
            if i + eff_mtu < len(data):
                await asyncio.sleep(0.05)
        self._ui_queue.put(("upload_complete",))

    def _on_notify(self, _sender: object, data: bytearray) -> None:
        if len(data) == 0:
            return
        if data[0] == RSP_ACK:
            ok = data[1] == 0 if len(data) > 1 else False
            self._ui_queue.put(("ack", ok))
        elif data[0] == RSP_STATUS and len(data) >= 6:
            state = PLAYBACK_STATES.get(data[1], f"UNKNOWN({data[1]})")
            step = data[2] | (data[3] << 8)
            total = data[4] | (data[5] << 8)
            self._ui_queue.put(("status_response", state, step, total))
        else:
            self._ui_queue.put(("notify_raw", data.hex()))


# ─── Main Application ────────────────────────────────────────────────────────

class ShinyHuntingApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Shiny Hunting Assistant Tool - Untitled")
        self.root.minsize(920, 680)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.model = ScriptModel()
        self.ble = BLEManager()
        self._ble_connected: bool = False
        self._selected_index: Optional[int] = None
        self._updating_editor: bool = False

        # Button checkbox vars
        self._btn_vars: dict[str, tk.BooleanVar] = {}

        # UI widgets (initialized in create methods)
        self.name_var: tk.StringVar
        self.name_entry: ttk.Entry
        self.btn_quick_save: ttk.Button
        self.btn_quick_save_as: ttk.Button
        self.btn_quick_open: ttk.Button
        self.btn_quick_new: ttk.Button
        self.tree: ttk.Treeview
        self.step_count_label: ttk.Label
        self.btn_add: ttk.Button
        self.btn_remove: ttk.Button
        self.btn_up: ttk.Button
        self.btn_down: ttk.Button
        self.btn_dup: ttk.Button
        self.delay_var: tk.IntVar
        self.delay_spin: ttk.Spinbox
        self.sx_var: tk.IntVar
        self.sy_var: tk.IntVar
        self.sx_scale: ttk.Scale
        self.sy_scale: ttk.Scale
        self.sx_spin: ttk.Spinbox
        self.sy_spin: ttk.Spinbox
        self.btn_scan: ttk.Button
        self.conn_status: ttk.Label
        self.device_combo: ttk.Combobox
        self.btn_connect: ttk.Button
        self.btn_disconnect: ttk.Button
        self.btn_upload: ttk.Button
        self.btn_play: ttk.Button
        self.btn_stop: ttk.Button
        self.btn_status: ttk.Button
        self.pico_status_label: ttk.Label
        self.status_var: tk.StringVar

        self._create_menu()
        self._create_connection_frame()
        self._create_main_area()
        self._create_control_frame()
        self._create_status_bar()

        self._poll_ble()
        self._update_title()
        self._update_button_states()

    # ── Menu ──

    def _create_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="New", accelerator="Ctrl+N", command=self._file_new)
        file_menu.add_command(label="Open...", accelerator="Ctrl+O", command=self._file_open)
        file_menu.add_separator()
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self._file_save)
        file_menu.add_command(label="Save As...", accelerator="Ctrl+Shift+S",
                              command=self._file_save_as)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menubar)

        self.root.bind("<Control-n>", lambda _e: self._file_new())
        self.root.bind("<Control-o>", lambda _e: self._file_open())
        self.root.bind("<Control-s>", lambda _e: self._file_save())
        self.root.bind("<Control-S>", lambda _e: self._file_save_as())

    # ── Connection Panel ──

    def _create_connection_frame(self) -> None:
        frame = ttk.LabelFrame(self.root, text="BLE Connection", padding=8)
        frame.pack(fill=tk.X, padx=8, pady=(8, 4))

        self.btn_scan = ttk.Button(frame, text="Scan", command=self._ble_scan)
        self.btn_scan.grid(row=0, column=0, padx=(0, 8))

        self.conn_status = ttk.Label(frame, text="Disconnected", foreground="gray")
        self.conn_status.grid(row=0, column=1, columnspan=3, sticky=tk.W)

        ttk.Label(frame, text="Device:").grid(row=1, column=0, sticky=tk.W, pady=(4, 0))
        self.device_combo = ttk.Combobox(frame, state="readonly", width=45)
        self.device_combo.grid(row=1, column=1, padx=4, pady=(4, 0), sticky=tk.W)

        self.btn_connect = ttk.Button(frame, text="Connect", command=self._ble_connect)
        self.btn_connect.grid(row=1, column=2, padx=4, pady=(4, 0))

        self.btn_disconnect = ttk.Button(frame, text="Disconnect",
                                          command=self._ble_disconnect, state=tk.DISABLED)
        self.btn_disconnect.grid(row=1, column=3, padx=4, pady=(4, 0))

    # ── Main Area (Script Table + Step Editor) ──

    def _create_main_area(self) -> None:
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Left: script table
        left = ttk.LabelFrame(main, text="Script", padding=4)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))

        # Script name + quick action toolbar
        toolbar = ttk.Frame(left)
        toolbar.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(toolbar, text="Name:").pack(side=tk.LEFT)
        self.name_var = tk.StringVar(value="Untitled")
        self.name_entry = ttk.Entry(toolbar, textvariable=self.name_var, width=20)
        self.name_entry.pack(side=tk.LEFT, padx=(4, 12))
        self.name_entry.bind("<KeyRelease>", self._on_name_change)

        self.btn_quick_save = ttk.Button(toolbar, text="Save", width=6,
                                          command=self._file_save)
        self.btn_quick_save.pack(side=tk.LEFT, padx=2)
        self.btn_quick_save_as = ttk.Button(toolbar, text="Save As...", width=8,
                                             command=self._file_save_as)
        self.btn_quick_save_as.pack(side=tk.LEFT, padx=2)
        self.btn_quick_open = ttk.Button(toolbar, text="Load Script...", width=12,
                                          command=self._file_open)
        self.btn_quick_open.pack(side=tk.LEFT, padx=2)
        self.btn_quick_new = ttk.Button(toolbar, text="New", width=5,
                                         command=self._file_new)
        self.btn_quick_new.pack(side=tk.LEFT, padx=2)

        # Treeview
        tree_cols = ("num", "delay", "buttons", "sx", "sy")
        self.tree = ttk.Treeview(left, columns=tree_cols, show="headings",
                                  selectmode="browse", height=14)
        self.tree.heading("num", text="#")
        self.tree.heading("delay", text="Delay (ms)")
        self.tree.heading("buttons", text="Buttons")
        self.tree.heading("sx", text="Stick X")
        self.tree.heading("sy", text="Stick Y")
        self.tree.column("num", width=40, anchor=tk.CENTER, stretch=False)
        self.tree.column("delay", width=80, anchor=tk.CENTER, stretch=False)
        self.tree.column("buttons", width=160, anchor=tk.W)
        self.tree.column("sx", width=60, anchor=tk.CENTER, stretch=False)
        self.tree.column("sy", width=60, anchor=tk.CENTER, stretch=False)

        scrollbar = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_step_select)

        # Step controls below the treeview container
        ctrl_row = ttk.Frame(left)
        ctrl_row.pack(fill=tk.X, pady=(4, 0))

        self.btn_add = ttk.Button(ctrl_row, text="Add Step", command=self._add_step)
        self.btn_add.pack(side=tk.LEFT, padx=2)
        self.btn_remove = ttk.Button(ctrl_row, text="Remove", command=self._remove_step)
        self.btn_remove.pack(side=tk.LEFT, padx=2)
        self.btn_up = ttk.Button(ctrl_row, text="Move Up",
                                  command=lambda: self._move_step(-1))
        self.btn_up.pack(side=tk.LEFT, padx=2)
        self.btn_down = ttk.Button(ctrl_row, text="Move Down",
                                    command=lambda: self._move_step(1))
        self.btn_down.pack(side=tk.LEFT, padx=2)
        self.btn_dup = ttk.Button(ctrl_row, text="Duplicate", command=self._duplicate_step)
        self.btn_dup.pack(side=tk.LEFT, padx=2)

        self.step_count_label = ttk.Label(ctrl_row, text="0 / 256 steps")
        self.step_count_label.pack(side=tk.RIGHT, padx=4)

        # Right: step editor
        right = ttk.LabelFrame(main, text="Step Editor", padding=8)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))

        self._create_step_editor(right)

    def _create_step_editor(self, parent: ttk.LabelFrame) -> None:
        # Delay
        delay_frame = ttk.Frame(parent)
        delay_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(delay_frame, text="Delay (ms):").pack(side=tk.LEFT)
        self.delay_var = tk.IntVar(value=1000)
        self.delay_spin = ttk.Spinbox(delay_frame, from_=0, to=60000, increment=50,
                                       textvariable=self.delay_var, width=8,
                                       command=self._on_editor_change)
        self.delay_spin.pack(side=tk.LEFT, padx=4)
        self.delay_spin.bind("<Return>", lambda _e: self._on_editor_change())
        self.delay_spin.bind("<FocusOut>", lambda _e: self._on_editor_change())

        # Buttons - GC controller layout
        btn_frame = ttk.LabelFrame(parent, text="Buttons", padding=6)
        btn_frame.pack(fill=tk.X, pady=(0, 8))

        # Triggers row
        trig = ttk.Frame(btn_frame)
        trig.pack(fill=tk.X, pady=2)
        self._make_btn_check(trig, "L").pack(side=tk.LEFT, padx=8)
        self._make_btn_check(trig, "Z").pack(side=tk.LEFT, padx=8)
        self._make_btn_check(trig, "R").pack(side=tk.LEFT, padx=8)

        # Two columns: D-pad on left, face buttons on right
        btn_cols = ttk.Frame(btn_frame)
        btn_cols.pack(fill=tk.X, pady=4)

        # D-pad column
        dpad = ttk.LabelFrame(btn_cols, text="D-Pad", padding=4)
        dpad.pack(side=tk.LEFT, padx=(0, 8), fill=tk.Y)

        dpad_grid = ttk.Frame(dpad)
        dpad_grid.pack()
        ttk.Label(dpad_grid, text="").grid(row=0, column=0)
        self._make_btn_check(dpad_grid, "D-Up").grid(row=0, column=1)
        ttk.Label(dpad_grid, text="").grid(row=0, column=2)
        self._make_btn_check(dpad_grid, "D-Left").grid(row=1, column=0)
        ttk.Label(dpad_grid, text="  ").grid(row=1, column=1)
        self._make_btn_check(dpad_grid, "D-Right").grid(row=1, column=2)
        ttk.Label(dpad_grid, text="").grid(row=2, column=0)
        self._make_btn_check(dpad_grid, "D-Down").grid(row=2, column=1)

        # Face buttons column
        face = ttk.LabelFrame(btn_cols, text="Face", padding=4)
        face.pack(side=tk.LEFT, fill=tk.Y)

        face_grid = ttk.Frame(face)
        face_grid.pack()
        ttk.Label(face_grid, text="").grid(row=0, column=0)
        self._make_btn_check(face_grid, "Y").grid(row=0, column=1)
        ttk.Label(face_grid, text="").grid(row=0, column=2)
        self._make_btn_check(face_grid, "X").grid(row=1, column=0)
        ttk.Label(face_grid, text="  ").grid(row=1, column=1)
        self._make_btn_check(face_grid, "A").grid(row=1, column=2)
        ttk.Label(face_grid, text="").grid(row=2, column=0)
        self._make_btn_check(face_grid, "B").grid(row=2, column=1)

        # Start
        start_frame = ttk.Frame(btn_frame)
        start_frame.pack(fill=tk.X, pady=2)
        self._make_btn_check(start_frame, "Start").pack(anchor=tk.CENTER)

        # Sticks
        stick_frame = ttk.LabelFrame(parent, text="Left Stick", padding=6)
        stick_frame.pack(fill=tk.X, pady=(0, 8))

        # X
        sx_row = ttk.Frame(stick_frame)
        sx_row.pack(fill=tk.X, pady=2)
        ttk.Label(sx_row, text="X:").pack(side=tk.LEFT)
        self.sx_var = tk.IntVar(value=128)
        self.sx_scale = ttk.Scale(sx_row, from_=0, to=255, orient=tk.HORIZONTAL,
                                   variable=self.sx_var,
                                   command=lambda _v: self._on_editor_change())
        self.sx_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self.sx_spin = ttk.Spinbox(sx_row, from_=0, to=255, textvariable=self.sx_var,
                                    width=4, command=self._on_editor_change)
        self.sx_spin.pack(side=tk.LEFT)
        self.sx_spin.bind("<Return>", lambda _e: self._on_editor_change())
        self.sx_spin.bind("<FocusOut>", lambda _e: self._on_editor_change())

        # Y
        sy_row = ttk.Frame(stick_frame)
        sy_row.pack(fill=tk.X, pady=2)
        ttk.Label(sy_row, text="Y:").pack(side=tk.LEFT)
        self.sy_var = tk.IntVar(value=128)
        self.sy_scale = ttk.Scale(sy_row, from_=0, to=255, orient=tk.HORIZONTAL,
                                   variable=self.sy_var,
                                   command=lambda _v: self._on_editor_change())
        self.sy_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self.sy_spin = ttk.Spinbox(sy_row, from_=0, to=255, textvariable=self.sy_var,
                                    width=4, command=self._on_editor_change)
        self.sy_spin.pack(side=tk.LEFT)
        self.sy_spin.bind("<Return>", lambda _e: self._on_editor_change())
        self.sy_spin.bind("<FocusOut>", lambda _e: self._on_editor_change())

        # Center button
        ttk.Button(stick_frame, text="Center (128, 128)",
                   command=self._center_stick).pack(pady=(4, 0))

    def _make_btn_check(self, parent: ttk.Frame | ttk.LabelFrame,
                         name: str) -> ttk.Checkbutton:
        var = tk.BooleanVar(value=False)
        self._btn_vars[name] = var
        cb = ttk.Checkbutton(parent, text=name, variable=var,
                              command=self._on_editor_change)
        return cb

    # ── Control Panel ──

    def _create_control_frame(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Playback Controls", padding=8)
        frame.pack(fill=tk.X, padx=8, pady=4)

        self.btn_upload = ttk.Button(frame, text="Upload Script",
                                      command=self._ble_upload)
        self.btn_upload.pack(side=tk.LEFT, padx=4)
        self.btn_play = ttk.Button(frame, text="Start Playback",
                                    command=self._ble_start)
        self.btn_play.pack(side=tk.LEFT, padx=4)
        self.btn_stop = ttk.Button(frame, text="Stop Playback",
                                    command=self._ble_stop)
        self.btn_stop.pack(side=tk.LEFT, padx=4)
        self.btn_status = ttk.Button(frame, text="Query Status",
                                      command=self._ble_query_status)
        self.btn_status.pack(side=tk.LEFT, padx=4)

        self.pico_status_label = ttk.Label(frame, text="Pico: --", foreground="gray")
        self.pico_status_label.pack(side=tk.RIGHT, padx=8)

    # ── Status Bar ──

    def _create_status_bar(self) -> None:
        self.status_var = tk.StringVar(value="Ready")
        bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN,
                         anchor=tk.W, padding=(8, 2))
        bar.pack(fill=tk.X, side=tk.BOTTOM)

    # ── Tree Refresh ──

    def _refresh_tree(self, select_index: Optional[int] = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(self.model.steps):
            self.tree.insert("", tk.END, iid=str(i), values=(
                i + 1,
                s["delay_ms"],
                buttons_to_str(s["buttons"]),
                s["stick_x"],
                s["stick_y"]
            ))
        self.step_count_label.config(text=f"{len(self.model.steps)} / {MAX_STEPS} steps")
        if select_index is not None and 0 <= select_index < len(self.model.steps):
            self.tree.selection_set(str(select_index))
            self.tree.see(str(select_index))
        # Sync name field
        self.name_var.set(self.model.name)
        self._update_title()
        self._update_button_states()

    # ── Step Selection & Editing ──

    def _on_step_select(self, _event: tk.Event | None = None) -> None:
        sel = self.tree.selection()
        if not sel:
            self._selected_index = None
            self._update_button_states()
            return
        self._selected_index = int(sel[0])
        self._load_step_to_editor()
        self._update_button_states()

    def _load_step_to_editor(self) -> None:
        if self._selected_index is None or self._selected_index >= len(self.model.steps):
            return
        self._updating_editor = True
        s = self.model.steps[self._selected_index]
        self.delay_var.set(s["delay_ms"])
        for btn_name, mask in BUTTONS.items():
            self._btn_vars[btn_name].set(bool(s["buttons"] & mask))
        self.sx_var.set(s["stick_x"])
        self.sy_var.set(s["stick_y"])
        self._updating_editor = False

    def _on_editor_change(self) -> None:
        if self._updating_editor or self._selected_index is None:
            return
        if self._selected_index >= len(self.model.steps):
            return
        # Build button bitmask from checkboxes
        bitmask = 0
        for btn_name, mask in BUTTONS.items():
            if self._btn_vars[btn_name].get():
                bitmask |= mask
        try:
            delay = int(self.delay_var.get())
        except (ValueError, tk.TclError):
            delay = 0
        try:
            sx = max(0, min(255, int(self.sx_var.get())))
        except (ValueError, tk.TclError):
            sx = 128
        try:
            sy = max(0, min(255, int(self.sy_var.get())))
        except (ValueError, tk.TclError):
            sy = 128

        self.model.update_step(self._selected_index,
                               delay_ms=delay, buttons=bitmask,
                               stick_x=sx, stick_y=sy)
        # Update tree row in place
        idx = self._selected_index
        s = self.model.steps[idx]
        self.tree.item(str(idx), values=(
            idx + 1,
            s["delay_ms"],
            buttons_to_str(s["buttons"]),
            s["stick_x"],
            s["stick_y"]
        ))
        self._update_title()

    def _on_name_change(self, _event: tk.Event | None = None) -> None:
        new_name = self.name_var.get().strip()
        if new_name and new_name != self.model.name:
            self.model.name = new_name
            self.model.dirty = True
            self._update_title()

    def _center_stick(self) -> None:
        self.sx_var.set(128)
        self.sy_var.set(128)
        self._on_editor_change()

    # ── Step CRUD ──

    def _add_step(self) -> None:
        idx = self._selected_index
        if idx is not None:
            insert_at = idx + 1
        else:
            insert_at = len(self.model.steps)
        if not self.model.add_step(index=insert_at):
            messagebox.showwarning("Limit Reached", f"Maximum {MAX_STEPS} steps allowed.")
            return
        self._refresh_tree(select_index=insert_at)

    def _remove_step(self) -> None:
        if self._selected_index is None:
            return
        idx = self._selected_index
        self.model.remove_step(idx)
        new_sel = min(idx, len(self.model.steps) - 1) if self.model.steps else None
        self._selected_index = new_sel
        self._refresh_tree(select_index=new_sel)

    def _move_step(self, direction: int) -> None:
        if self._selected_index is None:
            return
        new_idx = self.model.move_step(self._selected_index, direction)
        self._selected_index = new_idx
        self._refresh_tree(select_index=new_idx)

    def _duplicate_step(self) -> None:
        if self._selected_index is None:
            return
        if not self.model.duplicate_step(self._selected_index):
            messagebox.showwarning("Limit Reached", f"Maximum {MAX_STEPS} steps allowed.")
            return
        self._refresh_tree(select_index=self._selected_index + 1)

    # ── File Operations ──

    def _check_dirty(self) -> bool:
        """Returns True if OK to proceed, False if cancelled."""
        if not self.model.dirty:
            return True
        result = messagebox.askyesnocancel(
            "Unsaved Changes",
            "You have unsaved changes. Save before continuing?")
        if result is None:  # Cancel
            return False
        if result:  # Yes
            self._file_save()
            return not self.model.dirty  # False if save was cancelled
        return True  # No (discard)

    def _file_new(self) -> None:
        if not self._check_dirty():
            return
        self.model.new()
        self._selected_index = None
        self._refresh_tree()

    def _file_open(self) -> None:
        if not self._check_dirty():
            return
        filepath = filedialog.askopenfilename(
            title="Open Script",
            filetypes=[("JSON Scripts", "*.json"), ("All Files", "*.*")],
            defaultextension=".json")
        if not filepath:
            return
        try:
            self.model.load(filepath)
            self._selected_index = 0 if self.model.steps else None
            self._refresh_tree(select_index=self._selected_index)
            self._set_status(f"Opened: {os.path.basename(filepath)}")
        except Exception as e:
            messagebox.showerror("Open Error", f"Failed to load script:\n{e}")

    def _file_save(self) -> None:
        # Sync name from entry field before saving
        current_name = self.name_var.get().strip()
        if current_name:
            self.model.name = current_name
        if self.model.filepath:
            try:
                self.model.save(self.model.filepath)
                self._update_title()
                self._set_status(f"Saved: {self.model.name}")
            except Exception as e:
                messagebox.showerror("Save Error", f"Failed to save:\n{e}")
        else:
            self._file_save_as()

    def _file_save_as(self) -> None:
        current_name = self.name_var.get().strip() or "Untitled"
        filepath = filedialog.asksaveasfilename(
            title="Save Script As",
            filetypes=[("JSON Scripts", "*.json"), ("All Files", "*.*")],
            defaultextension=".json",
            initialfile=current_name + ".json")
        if not filepath:
            return
        try:
            self.model.name = current_name
            self.model.save(filepath)
            self._refresh_tree(select_index=self._selected_index)
            self._set_status(f"Saved: {current_name} -> {os.path.basename(filepath)}")
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save:\n{e}")

    # ── BLE Actions ──

    def _ble_scan(self) -> None:
        self.btn_scan.config(state=tk.DISABLED)
        self.device_combo.set("")
        self.device_combo["values"] = []
        self._set_status("Scanning for BLE devices...")
        self.ble.send_command("scan")

    def _ble_connect(self) -> None:
        sel = self.device_combo.get()
        if not sel:
            messagebox.showinfo("No Device", "Please scan and select a device first.")
            return
        try:
            address = sel.split("[")[1].rstrip("]")
        except IndexError:
            messagebox.showerror("Error", "Invalid device selection.")
            return
        self.ble.send_command("connect", address)

    def _ble_disconnect(self) -> None:
        self.ble.send_command("disconnect")

    def _ble_upload(self) -> None:
        if not self.model.steps:
            messagebox.showinfo("No Script", "Add some steps first.")
            return
        data = self.model.to_upload_bytes()
        self._set_status(f"Uploading {len(self.model.steps)} steps ({len(data)} bytes)...")
        self.ble.send_command("upload", data)

    def _ble_start(self) -> None:
        self.ble.send_command("start")
        self._set_status("Starting playback...")

    def _ble_stop(self) -> None:
        self.ble.send_command("stop")
        self._set_status("Stopping playback...")

    def _ble_query_status(self) -> None:
        self.ble.send_command("status")

    # ── BLE Message Handling ──

    def _poll_ble(self) -> None:
        try:
            while True:
                msg = self.ble.poll()
                self._handle_ble_msg(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_ble)

    def _handle_ble_msg(self, msg: tuple) -> None:
        kind = msg[0]

        if kind == "scan_started":
            self.conn_status.config(text="Scanning...", foreground="orange")

        elif kind == "scan_complete":
            devices = msg[1]
            self.btn_scan.config(state=tk.NORMAL)
            if devices:
                entries = [f"{dev_name} [{addr}]" for dev_name, addr in devices]
                self.device_combo["values"] = entries
                self.device_combo.current(0)
                self.conn_status.config(text=f"Found {len(devices)} device(s)",
                                         foreground="green")
                self._set_status(f"Scan complete: {len(devices)} device(s) found")
            else:
                self.conn_status.config(text="No devices found", foreground="red")
                self._set_status("Scan complete: no compatible devices found")

        elif kind == "connecting":
            self.conn_status.config(text="Connecting...", foreground="orange")
            self._set_status("Connecting...")

        elif kind == "connected":
            mtu = msg[1]
            self._ble_connected = True
            self.conn_status.config(text=f"Connected (MTU={mtu})", foreground="green")
            self._set_status(f"Connected! MTU={mtu}")
            self._update_button_states()

        elif kind == "disconnected":
            self._ble_connected = False
            self.conn_status.config(text="Disconnected", foreground="gray")
            self.pico_status_label.config(text="Pico: --", foreground="gray")
            self._set_status("Disconnected")
            self._update_button_states()

        elif kind == "error":
            self._set_status(f"Error: {msg[1]}")
            if "not connected" in str(msg[1]).lower() or "disconnect" in str(msg[1]).lower():
                self._ble_connected = False
                self.conn_status.config(text="Disconnected", foreground="gray")
                self._update_button_states()
            else:
                messagebox.showerror("BLE Error", str(msg[1]))

        elif kind == "upload_started":
            total_bytes, eff_mtu = msg[1], msg[2]
            self._set_status(f"Uploading {total_bytes} bytes (MTU payload={eff_mtu})...")

        elif kind == "upload_complete":
            self._set_status("Upload complete! Waiting for ACK...")

        elif kind == "ack":
            ok = msg[1]
            if ok:
                self._set_status("Pico acknowledged: OK")
            else:
                self._set_status("Pico responded: ERROR")
                messagebox.showwarning("Pico Error", "The Pico reported an error.")

        elif kind == "status_response":
            state, step, total = msg[1], msg[2], msg[3]
            self.pico_status_label.config(
                text=f"Pico: {state} (step {step}/{total})",
                foreground="green" if state == "IDLE" else "blue")
            self._set_status(f"Pico status: {state}, step {step}/{total}")

        elif kind == "notify_raw":
            self._set_status(f"Pico notification: {msg[1]}")

    # ── UI State ──

    def _update_title(self) -> None:
        script_name = self.model.name or "Untitled"
        file_info = ""
        if self.model.filepath:
            file_info = f" ({os.path.basename(self.model.filepath)})"
        dirty = "*" if self.model.dirty else ""
        self.root.title(f"{dirty}{script_name}{file_info} - Shiny Hunting Assistant Tool")

    def _update_button_states(self) -> None:
        has_sel = self._selected_index is not None
        has_steps = len(self.model.steps) > 0
        connected = self._ble_connected

        # Step controls
        state_sel = tk.NORMAL if has_sel else tk.DISABLED
        self.btn_remove.config(state=state_sel)
        self.btn_up.config(state=state_sel)
        self.btn_down.config(state=state_sel)
        self.btn_dup.config(state=state_sel)

        self.btn_add.config(
            state=tk.NORMAL if len(self.model.steps) < MAX_STEPS else tk.DISABLED)

        # BLE controls
        self.btn_scan.config(state=tk.NORMAL if not connected else tk.DISABLED)
        self.btn_connect.config(state=tk.NORMAL if not connected else tk.DISABLED)
        self.btn_disconnect.config(state=tk.NORMAL if connected else tk.DISABLED)
        self.btn_upload.config(state=tk.NORMAL if connected and has_steps else tk.DISABLED)
        self.btn_play.config(state=tk.NORMAL if connected else tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL if connected else tk.DISABLED)
        self.btn_status.config(state=tk.NORMAL if connected else tk.DISABLED)

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    # ── Lifecycle ──

    def _on_close(self) -> None:
        if not self._check_dirty():
            return
        if self._ble_connected:
            self.ble.send_command("disconnect")
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = ShinyHuntingApp()
    app.run()
