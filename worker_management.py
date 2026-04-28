import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import logging
import threading
import time
from rfid_reader import RFIDReader
from config import CONFIG
import os # Added for log file path

logger = logging.getLogger(__name__)

class WorkerManagementGUI:
    def __init__(self, parent, database):
        """Initialize Worker Management GUI"""
        self.parent = parent
        self.db = database

        # Create a new top-level window
        self.window = tk.Toplevel(parent)
        self.window.geometry("700x900")  # Updated for portrait orientation
        self.window.title("Worker Management")
        self.window.minsize(600, 800)  # Updated minimum size for portrait
        self.window.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Apple-inspired color palette (matching main GUI)
        self.colors = {
            'bg_primary': '#F2F2F7',        # Light gray background
            'bg_secondary': '#FFFFFF',       # Pure white
            'bg_tertiary': '#F8F9FA',       # Slightly off-white
            'card_bg': '#FFFFFF',           # White cards
            'card_shadow': '#E5E5EA',       # Subtle shadow
            'text_primary': '#1D1D1F',      # Near black
            'text_secondary': '#86868B',     # Gray
            'text_tertiary': '#C7C7CC',     # Light gray
            'accent_blue': '#007AFF',       # iOS blue
            'accent_green': '#30D158',      # iOS green  
            'accent_orange': '#FF9500',     # iOS orange
            'accent_red': '#FF3B30',        # iOS red
            'success': '#30D158',
            'warning': '#FF9500', 
            'error': '#FF3B30',
            'info': '#007AFF',
            'hover_light': '#F0F0F5',
            'hover_medium': '#E8E8ED',
            'pressed': '#D1D1D6'
        }

        # Configure window background
        self.window.configure(bg=self.colors['bg_primary'])

        # Apply Premium Theming
        self.style = ttk.Style(self.window)
        try:
            self.style.theme_use('clam')
        except tk.TclError:
            logger.warning("Failed to set 'clam' theme, using default.")
            self.style.theme_use('default')

        self._setup_premium_styles()

        # Initialize RFID reader
        self.rfid_reader = None
        self.scanning_active = False
        self.scan_target = None  # 'worker' or 'washing'

        # Create the interface
        self.create_interface()

        # Populate initial data
        self.refresh_data()

    def _setup_premium_styles(self):
        """Setup Apple-inspired styling for worker management"""
        try:
            # Check if SF Pro Display font exists
            def font_exists(font_name):
                try:
                    import tkinter.font as tkFont
                    return font_name in tkFont.families()
                except:
                    return False

            base_font = ('SF Pro Display', 10) if font_exists('SF Pro Display') else ('Segoe UI', 10)
            heading_font = ('SF Pro Display', 11, 'bold') if font_exists('SF Pro Display') else ('Segoe UI', 11, 'bold')

            # Base styles
            self.style.configure('.', 
                background=self.colors['bg_primary'], 
                foreground=self.colors['text_primary'], 
                font=base_font)

            # Frame styles
            self.style.configure('TFrame', background=self.colors['bg_primary'], borderwidth=0)
            self.style.configure('Card.TFrame', 
                background=self.colors['card_bg'], 
                relief='flat', 
                borderwidth=1,
                padding=15)

            # Label styles
            self.style.configure('TLabel', 
                background=self.colors['bg_primary'], 
                foreground=self.colors['text_primary'],
                font=base_font)
            self.style.configure('Card.TLabel', 
                background=self.colors['card_bg'], 
                foreground=self.colors['text_primary'])
            self.style.configure('Heading.TLabel', 
                font=heading_font,
                background=self.colors['bg_primary'],
                foreground=self.colors['text_primary'])

            # Button styles
            self.style.configure('TButton', 
                padding=(12, 8), 
                relief='flat', 
                font=base_font,
                borderwidth=0)
            self.style.configure('Primary.TButton',
                background=self.colors['accent_blue'],
                foreground='white',
                font=heading_font)
            self.style.map('Primary.TButton',
                background=[('active', '#0066CC'), ('pressed', '#004499')])
            self.style.configure('Secondary.TButton',
                background=self.colors['hover_medium'],
                foreground=self.colors['text_primary'])
            self.style.map('Secondary.TButton',
                background=[('active', self.colors['hover_light']), ('pressed', self.colors['pressed'])])
            self.style.configure('Success.TButton',
                background=self.colors['success'],
                foreground='white',
                font=heading_font)
            self.style.map('Success.TButton',
                background=[('active', '#28B946'), ('pressed', '#20A93E')])

            # Entry styles
            self.style.configure('TEntry',
                fieldbackground=self.colors['card_bg'],
                borderwidth=1,
                relief='solid',
                padding=8)

            # LabelFrame styles
            self.style.configure('TLabelframe', 
                background=self.colors['card_bg'], 
                borderwidth=1, 
                relief="flat")
            self.style.configure('TLabelframe.Label', 
                background=self.colors['card_bg'], 
                foreground=self.colors['text_primary'], 
                font=heading_font)

            # Treeview styles
            self.style.configure('Treeview.Heading', 
                font=heading_font, 
                background=self.colors['bg_tertiary'], 
                foreground=self.colors['text_primary'],
                relief='flat')
            self.style.map('Treeview.Heading', 
                relief=[('active','flat'),('pressed','flat')])
            self.style.configure('Treeview', 
                fieldbackground=self.colors['card_bg'], 
                background=self.colors['card_bg'],
                foreground=self.colors['text_primary'],
                borderwidth=0,
                rowheight=26)

            # Notebook styles
            self.style.configure('TNotebook', 
                background=self.colors['bg_primary'],
                borderwidth=0,
                padding=0)
            self.style.configure('TNotebook.Tab', 
                background=self.colors['hover_medium'],
                foreground=self.colors['text_primary'],
                padding=(20, 12),
                font=base_font)
            self.style.map('TNotebook.Tab',
                background=[('selected', self.colors['card_bg']),
                           ('active', self.colors['hover_light'])])

        except Exception as e:
            logger.error(f"Error configuring premium worker management theme: {e}", exc_info=True)

    def create_interface(self):
        """Create the worker management interface"""
        # Main frame with premium styling
        main_frame = tk.Frame(self.window, bg=self.colors['bg_primary'])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        # Title with premium typography
        title_label = tk.Label(main_frame, 
                              text="Worker & Gown Management", 
                              font=('SF Pro Display', 20, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 20, 'bold'),
                              fg=self.colors['text_primary'],
                              bg=self.colors['bg_primary'])
        title_label.pack(pady=(0, 25))

        # Tab control with modern styling
        self.tab_control = ttk.Notebook(main_frame)

        # Worker-Gown tab
        self.worker_tab = tk.Frame(self.tab_control, bg=self.colors['bg_primary'])
        self.tab_control.add(self.worker_tab, text="  Workers & Gowns  ")
        self.setup_worker_tab(self.worker_tab)

        # Washing Records tab
        self.washing_tab = tk.Frame(self.tab_control, bg=self.colors['bg_primary'])
        self.tab_control.add(self.washing_tab, text="  Washing Records  ")
        self.setup_washing_tab(self.washing_tab)

        # Admin tab
        self.admin_tab = tk.Frame(self.tab_control, bg=self.colors['bg_primary'])
        self.tab_control.add(self.admin_tab, text="  Admin Settings  ")
        self.setup_admin_tab(self.admin_tab)

        self.tab_control.pack(expand=True, fill="both", pady=(0, 20))

        # Status bar with modern styling
        status_frame = tk.Frame(main_frame, bg=self.colors['card_bg'], relief='flat', bd=1)
        status_frame.pack(fill=tk.X, pady=(0, 10))

        self.status_var = tk.StringVar(value="Ready")
        self.status_label = tk.Label(status_frame, 
                                    textvariable=self.status_var, 
                                    relief='flat',
                                    anchor=tk.W, 
                                    bg=self.colors['card_bg'],
                                    fg=self.colors['text_secondary'],
                                    font=('SF Pro Display', 10) if self._font_exists('SF Pro Display') else ('Segoe UI', 10),
                                    padx=15, pady=8)
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Search box with modern styling
        search_container = tk.Frame(status_frame, bg=self.colors['card_bg'])
        search_container.pack(side=tk.RIGHT, padx=15, pady=5)

        tk.Label(search_container, text="Search:", 
                bg=self.colors['card_bg'], fg=self.colors['text_secondary'],
                font=('SF Pro Display', 10) if self._font_exists('SF Pro Display') else ('Segoe UI', 10)).pack(side=tk.LEFT, padx=(0, 8))

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.on_search_changed)
        search_entry = tk.Entry(search_container, textvariable=self.search_var, 
                               width=20, relief='flat', bd=1,
                               bg=self.colors['card_bg'], fg=self.colors['text_primary'],
                               font=('SF Pro Display', 10) if self._font_exists('SF Pro Display') else ('Segoe UI', 10))
        search_entry.pack(side=tk.RIGHT, ipady=4)

        # Bottom buttons with premium styling
        button_frame = tk.Frame(main_frame, bg=self.colors['bg_primary'])
        button_frame.pack(fill=tk.X)

        ttk.Button(button_frame, text="Refresh Data", 
                  command=self.refresh_data, 
                  style='Secondary.TButton').pack(side=tk.LEFT)
        ttk.Button(button_frame, text="Close", 
                  command=self.on_closing, 
                  style='Secondary.TButton').pack(side=tk.RIGHT)

    def _font_exists(self, font_name):
        """Check if a font exists on the system"""
        try:
            import tkinter.font as tkFont
            return font_name in tkFont.families()
        except:
            return False

    def setup_worker_tab(self, parent):
        """Set up the worker-gown assignment tab"""
        # Container with padding
        container = tk.Frame(parent, bg=self.colors['bg_primary'])
        container.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

        # Add new worker-gown card
        add_card = tk.Frame(container, bg=self.colors['card_bg'], relief='flat', bd=1)
        add_card.pack(fill=tk.X, pady=(0, 20))

        # Card header
        header_frame = tk.Frame(add_card, bg=self.colors['card_bg'])
        header_frame.pack(fill=tk.X, padx=20, pady=(15, 10))
        
        tk.Label(header_frame, 
                text="Add New Worker-Gown Association",
                font=('SF Pro Display', 14, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 14, 'bold'),
                bg=self.colors['card_bg'], fg=self.colors['text_primary']).pack(side=tk.LEFT)

        # Form container
        form_frame = tk.Frame(add_card, bg=self.colors['card_bg'])
        form_frame.pack(fill=tk.X, padx=20, pady=(0, 20))
        form_frame.columnconfigure(1, weight=1)
        form_frame.columnconfigure(3, weight=1)

        # Serial Number field
        tk.Label(form_frame, text="Serial Number:", 
                bg=self.colors['card_bg'], fg=self.colors['text_secondary'],
                font=('SF Pro Display', 11) if self._font_exists('SF Pro Display') else ('Segoe UI', 11)).grid(
                row=0, column=0, sticky=tk.W, padx=(0, 10), pady=8)
        
        self.serial_entry = tk.Entry(form_frame, width=25, relief='flat', bd=1,
                                    bg='white', fg=self.colors['text_primary'],
                                    font=('SF Pro Display', 11) if self._font_exists('SF Pro Display') else ('Segoe UI', 11))
        self.serial_entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 20), pady=8, ipady=6)
        self.serial_entry.bind("<Return>", lambda event: self.worker_entry.focus_set())

        # Worker Name field
        tk.Label(form_frame, text="Worker Name:", 
                bg=self.colors['card_bg'], fg=self.colors['text_secondary'],
                font=('SF Pro Display', 11) if self._font_exists('SF Pro Display') else ('Segoe UI', 11)).grid(
                row=0, column=2, sticky=tk.W, padx=(0, 10), pady=8)
        
        self.worker_entry = tk.Entry(form_frame, width=35, relief='flat', bd=1,
                                    bg='white', fg=self.colors['text_primary'],
                                    font=('SF Pro Display', 11) if self._font_exists('SF Pro Display') else ('Segoe UI', 11))
        self.worker_entry.grid(row=0, column=3, sticky=tk.EW, pady=8, ipady=6)
        self.worker_entry.bind("<Return>", lambda event: self.add_worker_gown())

        # Buttons
        button_container = tk.Frame(form_frame, bg=self.colors['card_bg'])
        button_container.grid(row=1, column=0, columnspan=4, pady=(15, 0), sticky=tk.E)

        self.scan_worker_button = ttk.Button(button_container, text="Scan RFID", 
                                           command=self.scan_rfid, style='Secondary.TButton')
        self.scan_worker_button.pack(side=tk.RIGHT, padx=(0, 10))

        self.add_button = ttk.Button(button_container, text="Add Association", 
                                   command=self.add_worker_gown, style='Primary.TButton')
        self.add_button.pack(side=tk.RIGHT)

        # Table container
        table_container = tk.Frame(container, bg=self.colors['card_bg'], relief='flat', bd=1)
        table_container.pack(fill=tk.BOTH, expand=True)

        # Table header
        table_header = tk.Frame(table_container, bg=self.colors['card_bg'])
        table_header.pack(fill=tk.X, padx=20, pady=(15, 10))
        
        tk.Label(table_header, 
                text="Current Associations",
                font=('SF Pro Display', 14, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 14, 'bold'),
                bg=self.colors['card_bg'], fg=self.colors['text_primary']).pack(side=tk.LEFT)

        # Table frame
        table_frame = tk.Frame(table_container, bg=self.colors['card_bg'])
        table_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))

        # Create Treeview
        columns = ("serial", "worker", "registered", "first_use")
        self.worker_table = ttk.Treeview(table_frame, columns=columns, show="headings")

        # Define headings
        self.worker_table.heading("serial", text="Serial Number", anchor=tk.W, 
                                 command=lambda: self.sort_treeview(self.worker_table, "serial", False))
        self.worker_table.heading("worker", text="Worker Name", anchor=tk.W, 
                                 command=lambda: self.sort_treeview(self.worker_table, "worker", False))
        self.worker_table.heading("registered", text="Registered", anchor=tk.W, 
                                 command=lambda: self.sort_treeview(self.worker_table, "registered", False))
        self.worker_table.heading("first_use", text="First Use", anchor=tk.W, 
                                 command=lambda: self.sort_treeview(self.worker_table, "first_use", False))

        # Define columns
        self.worker_table.column("serial", width=150, anchor=tk.W, stretch=False)
        self.worker_table.column("worker", width=200, anchor=tk.W, stretch=True)
        self.worker_table.column("registered", width=150, anchor=tk.W, stretch=False)
        self.worker_table.column("first_use", width=150, anchor=tk.W, stretch=False)

        # Add scrollbars
        y_scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.worker_table.yview)
        self.worker_table.configure(yscroll=y_scrollbar.set)

        x_scrollbar = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.worker_table.xview)
        self.worker_table.configure(xscroll=x_scrollbar.set)

        # Pack widgets
        y_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        x_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.worker_table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Add context menu
        self.worker_table.bind("<Button-3>", self.show_worker_menu)

        # Create right-click menu
        self.worker_menu = tk.Menu(self.window, tearoff=0)
        self.worker_menu.add_command(label="Record Washing", command=self.record_washing)
        self.worker_menu.add_separator()
        self.worker_menu.add_command(label="Edit Worker Name", command=self.edit_worker_name)
        self.worker_menu.add_command(label="Delete Association", command=self.delete_worker_gown)
        self.worker_menu.add_command(label="Reset First Use Date", command=self.reset_first_use)

        # Table management buttons container
        table_mgmt_container = tk.Frame(table_container, bg=self.colors['card_bg'])
        table_mgmt_container.pack(fill=tk.X, padx=20, pady=(0, 10))

        # Reset First Use button
        self.reset_first_use_button = ttk.Button(table_mgmt_container, text="Reset First Use", 
                                               command=self.reset_first_use, style='Secondary.TButton')
        self.reset_first_use_button.pack(side=tk.RIGHT)

    def setup_washing_tab(self, parent):
        """Set up the washing records tab"""
        # Container with padding
        container = tk.Frame(parent, bg=self.colors['bg_primary'])
        container.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

        # Record washing card
        washing_card = tk.Frame(container, bg=self.colors['card_bg'], relief='flat', bd=1)
        washing_card.pack(fill=tk.X, pady=(0, 20))

        # Card header
        header_frame = tk.Frame(washing_card, bg=self.colors['card_bg'])
        header_frame.pack(fill=tk.X, padx=20, pady=(15, 10))
        
        tk.Label(header_frame, 
                text="Record New Washing",
                font=('SF Pro Display', 14, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 14, 'bold'),
                bg=self.colors['card_bg'], fg=self.colors['text_primary']).pack(side=tk.LEFT)

        # Form container
        form_frame = tk.Frame(washing_card, bg=self.colors['card_bg'])
        form_frame.pack(fill=tk.X, padx=20, pady=(0, 20))
        form_frame.columnconfigure(1, weight=1)

        # Serial Number field
        tk.Label(form_frame, text="Serial Number:", 
                bg=self.colors['card_bg'], fg=self.colors['text_secondary'],
                font=('SF Pro Display', 11) if self._font_exists('SF Pro Display') else ('Segoe UI', 11)).grid(
                row=0, column=0, sticky=tk.W, padx=(0, 10), pady=8)
        
        self.wash_serial_entry = tk.Entry(form_frame, width=25, relief='flat', bd=1,
                                         bg='white', fg=self.colors['text_primary'],
                                         font=('SF Pro Display', 11) if self._font_exists('SF Pro Display') else ('Segoe UI', 11))
        self.wash_serial_entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 15), pady=8, ipady=6)
        self.wash_serial_entry.bind("<Return>", lambda event: self.add_washing_record())

        # Buttons
        button_container = tk.Frame(form_frame, bg=self.colors['card_bg'])
        button_container.grid(row=0, column=2, padx=(15, 0), pady=8)

        self.scan_washing_button = ttk.Button(button_container, text="Scan RFID", 
                                            command=self.scan_rfid_for_washing, style='Secondary.TButton')
        self.scan_washing_button.pack(side=tk.LEFT, padx=(0, 10))

        self.wash_button = ttk.Button(button_container, text="Record Washing", 
                                    command=self.add_washing_record, style='Success.TButton')
        self.wash_button.pack(side=tk.LEFT)

        # Table container
        table_container = tk.Frame(container, bg=self.colors['card_bg'], relief='flat', bd=1)
        table_container.pack(fill=tk.BOTH, expand=True)

        # Table header
        table_header = tk.Frame(table_container, bg=self.colors['card_bg'])
        table_header.pack(fill=tk.X, padx=20, pady=(15, 10))
        
        tk.Label(table_header, 
                text="Washing History",
                font=('SF Pro Display', 14, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 14, 'bold'),
                bg=self.colors['card_bg'], fg=self.colors['text_primary']).pack(side=tk.LEFT)

        # Table frame
        table_frame = tk.Frame(table_container, bg=self.colors['card_bg'])
        table_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))

        # Create Treeview
        columns = ("serial", "timestamp", "worker")
        self.washing_table = ttk.Treeview(table_frame, columns=columns, show="headings")

        # Define headings
        self.washing_table.heading("serial", text="Serial Number", anchor=tk.W, 
                                  command=lambda: self.sort_treeview(self.washing_table, "serial", False))
        self.washing_table.heading("timestamp", text="Washing Date/Time", anchor=tk.W, 
                                  command=lambda: self.sort_treeview(self.washing_table, "timestamp", False))
        self.washing_table.heading("worker", text="Worker Name", anchor=tk.W, 
                                  command=lambda: self.sort_treeview(self.washing_table, "worker", False))

        # Define columns
        self.washing_table.column("serial", width=150, anchor=tk.W, stretch=False)
        self.washing_table.column("timestamp", width=200, anchor=tk.W, stretch=True)
        self.washing_table.column("worker", width=150, anchor=tk.W, stretch=False)

        # Add scrollbars
        y_scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.washing_table.yview)
        self.washing_table.configure(yscroll=y_scrollbar.set)

        x_scrollbar = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.washing_table.xview)
        self.washing_table.configure(xscroll=x_scrollbar.set)

        # Pack widgets
        y_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        x_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.washing_table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def setup_admin_tab(self, parent):
        """Set up the admin tab"""
        # Container with padding
        container = tk.Frame(parent, bg=self.colors['bg_primary'])
        container.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

        # Title
        tk.Label(container, text="System Administration", 
                font=('SF Pro Display', 18, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 18, 'bold'),
                bg=self.colors['bg_primary'], fg=self.colors['text_primary']).pack(pady=(0, 25))

        # System Options card
        options_card = tk.Frame(container, bg=self.colors['card_bg'], relief='flat', bd=1)
        options_card.pack(fill=tk.X, pady=(0, 20))

        # Card header
        header_frame = tk.Frame(options_card, bg=self.colors['card_bg'])
        header_frame.pack(fill=tk.X, padx=20, pady=(15, 10))
        
        tk.Label(header_frame, 
                text="Database Management",
                font=('SF Pro Display', 14, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 14, 'bold'),
                bg=self.colors['card_bg'], fg=self.colors['text_primary']).pack(side=tk.LEFT)

        # Backup/restore buttons
        button_frame = tk.Frame(options_card, bg=self.colors['card_bg'])
        button_frame.pack(fill=tk.X, padx=20, pady=(0, 20))

        ttk.Button(button_frame, text="Backup Database", 
                  command=self.backup_database, style='Secondary.TButton').pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(button_frame, text="Restore Database", 
                  command=self.restore_database, style='Secondary.TButton').pack(side=tk.LEFT)

        # Advanced settings card
        advanced_card = tk.Frame(container, bg=self.colors['card_bg'], relief='flat', bd=1)
        advanced_card.pack(fill=tk.X, pady=(0, 20))

        # Card header
        header_frame = tk.Frame(advanced_card, bg=self.colors['card_bg'])
        header_frame.pack(fill=tk.X, padx=20, pady=(15, 10))
        
        tk.Label(header_frame, 
                text="System Settings",
                font=('SF Pro Display', 14, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 14, 'bold'),
                bg=self.colors['card_bg'], fg=self.colors['text_primary']).pack(side=tk.LEFT)

        # Settings container
        settings_frame = tk.Frame(advanced_card, bg=self.colors['card_bg'])
        settings_frame.pack(fill=tk.X, padx=20, pady=(0, 20))

        # Mock mode checkbox
        self.mock_rfid_var = tk.BooleanVar(value=CONFIG.get('rfid', {}).get('enable_mock', False))
        checkbox_frame = tk.Frame(settings_frame, bg=self.colors['card_bg'])
        checkbox_frame.pack(fill=tk.X, pady=5)

        ttk.Checkbutton(checkbox_frame, text="Enable Mock RFID Reader", 
                       variable=self.mock_rfid_var,
                       command=lambda: self.update_config('rfid.enable_mock', self.mock_rfid_var.get())).pack(side=tk.LEFT)

        # Logs button
        logs_frame = tk.Frame(container, bg=self.colors['bg_primary'])
        logs_frame.pack(fill=tk.X)
        
        ttk.Button(logs_frame, text="View System Logs", 
                  command=self.view_logs, style='Secondary.TButton').pack()

    # === Keep all existing methods with minimal changes ===
    def add_worker_gown(self, event=None):
        """Add a new worker-gown association"""
        self.add_button.config(state="disabled")

        serial = self.serial_entry.get().strip()
        worker = self.worker_entry.get().strip()

        if not serial or not worker:
            messagebox.showerror("Error", "Both Serial Number and Worker Name are required", parent=self.window)
            self.add_button.config(state="normal")
            return

        try:
            from config import sanitize_serial_number, sanitize_worker_name
            serial = sanitize_serial_number(serial)
            worker = sanitize_worker_name(worker)
        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Input validation failed: {str(e)}", parent=self.window)
            self.add_button.config(state="normal")
            return

        try:
            if self.db.add_worker_gown(serial, worker):
                messagebox.showinfo("Success", f"Worker-gown association added: {worker} - {serial}", parent=self.window)
                self.serial_entry.delete(0, tk.END)
                self.worker_entry.delete(0, tk.END)
                self.refresh_data()
                self.status_var.set(f"Added: {worker} - {serial}")
            else:
                messagebox.showerror("Error", "Failed to add worker-gown association", parent=self.window)
                self.status_var.set("Error adding association")
        except Exception as e:
            logger.error(f"Error adding worker-gown: {e}", exc_info=True)
            messagebox.showerror("Error", f"An error occurred: {str(e)}", parent=self.window)
            self.status_var.set("Error adding association")

        self.add_button.config(state="normal")
        self.serial_entry.focus_set()

    def add_washing_record(self, event=None):
        """Add a new washing record"""
        self.wash_button.config(state="disabled")

        serial = self.wash_serial_entry.get().strip()

        if not serial:
            messagebox.showerror("Error", "Serial Number is required", parent=self.window)
            self.wash_button.config(state="normal")
            return

        try:
            from config import sanitize_serial_number
            serial = sanitize_serial_number(serial)
        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Serial number validation failed: {str(e)}", parent=self.window)
            self.wash_button.config(state="normal")
            return

        try:
            if self.db.add_washing_record(serial):
                messagebox.showinfo("Success", f"Washing record added for gown: {serial}", parent=self.window)
                self.wash_serial_entry.delete(0, tk.END)
                self.refresh_data()
                self.status_var.set(f"Added washing record for: {serial}")
            else:
                messagebox.showerror("Error", "Failed to add washing record", parent=self.window)
                self.status_var.set("Error adding washing record")
        except Exception as e:
            logger.error(f"Error adding washing record: {e}", exc_info=True)
            messagebox.showerror("Error", f"An error occurred: {str(e)}", parent=self.window)
            self.status_var.set("Error adding washing record")

        self.wash_button.config(state="normal")
        self.wash_serial_entry.focus_set()

    def init_rfid_reader(self):
        """Initialize the RFID reader if not already initialized"""
        if self.rfid_reader is None:
            try:
                self.rfid_reader = RFIDReader()
                logger.info("RFID reader initialized for Worker Management")
                return True
            except Exception as e:
                logger.error(f"Error initializing RFID reader: {e}", exc_info=True)
                if messagebox.askyesno("RFID Error",
                                      f"Failed to initialize RFID reader: {str(e)}\n\n"
                                      "Do you want to enable manual input mode?", parent=self.window):
                    self.rfid_reader = RFIDReader()
                    self.rfid_reader.mock_mode = True
                    logger.info("RFID reader initialized in mock mode for Worker Management")
                    return True
                else:
                    messagebox.showerror("RFID Error", "RFID reader initialization failed", parent=self.window)
                    return False
        return True

    def scan_rfid(self):
        """Scan RFID for worker-gown association"""
        self.stop_rfid_scanning()

        if not self.init_rfid_reader():
            return

        self.scan_worker_button.config(text="Stop Scanning", command=self.stop_rfid_scanning)
        self.status_var.set("Scanning for RFID tag... (Worker-Gown)")
        self.scan_target = 'worker'
        self.scanning_active = True

        if self.rfid_reader.mock_mode:
            serial = simpledialog.askstring("RFID Input", "Enter RFID Serial Number:", parent=self.window)
            if serial:
                self.process_scanned_tag(serial.strip())
            else:
                self.stop_rfid_scanning()
            return

        try:
            success = self.rfid_reader.start(self.on_rfid_tag_detected)
            if not success:
                self.stop_rfid_scanning()
                messagebox.showerror("RFID Error", "Failed to start RFID reader", parent=self.window)
        except Exception as e:
            logger.error(f"Error starting RFID reader: {e}", exc_info=True)
            self.stop_rfid_scanning()
            messagebox.showerror("RFID Error", f"Error starting RFID reader: {str(e)}", parent=self.window)

    def scan_rfid_for_washing(self):
        """Scan RFID for washing record"""
        self.stop_rfid_scanning()

        if not self.init_rfid_reader():
            return

        self.scan_washing_button.config(text="Stop Scanning", command=self.stop_rfid_scanning)
        self.status_var.set("Scanning for RFID tag... (Washing Record)")
        self.scan_target = 'washing'
        self.scanning_active = True

        if self.rfid_reader.mock_mode:
            serial = simpledialog.askstring("RFID Input", "Enter RFID Serial Number:", parent=self.window)
            if serial:
                self.process_scanned_tag(serial.strip())
            else:
                self.stop_rfid_scanning()
            return

        try:
            success = self.rfid_reader.start(self.on_rfid_tag_detected)
            if not success:
                self.stop_rfid_scanning()
                messagebox.showerror("RFID Error", "Failed to start RFID reader", parent=self.window)
        except Exception as e:
            logger.error(f"Error starting RFID reader: {e}", exc_info=True)
            self.stop_rfid_scanning()
            messagebox.showerror("RFID Error", f"Error starting RFID reader: {str(e)}", parent=self.window)

    def on_rfid_tag_detected(self, serial_number):
        """Callback for when an RFID tag is detected"""
        if not self.scanning_active:
            logger.debug("RFID detected but scanning not active, ignoring.")
            return

        logger.info(f"RFID tag detected in WorkerManagement: {serial_number}")

        if self.window.winfo_exists():
            self.window.after(0, self.process_scanned_tag, serial_number)
        else:
            logger.warning("Worker Management window closed before RFID tag could be processed.")
            self.stop_rfid_scanning()

    def process_scanned_tag(self, serial_number):
        """Process the scanned tag in the main thread"""
        logger.debug(f"Processing scanned tag '{serial_number}' for target '{self.scan_target}' IN MAIN THREAD")

        if not self.window.winfo_exists():
            logger.warning("Worker Management window closed, cannot process scanned tag.")
            return

        try:
            if self.scan_target == 'worker':
                logger.debug("Processing for 'worker' target.")
                if hasattr(self, 'serial_entry') and self.serial_entry.winfo_exists():
                    self.serial_entry.delete(0, tk.END)
                    self.serial_entry.insert(0, serial_number)
                    self.worker_entry.focus_set()
                    self.status_var.set(f"Tag scanned: {serial_number}")
                    logger.debug(f"Updated worker serial entry with {serial_number}")

                    worker_name = self.db.get_worker_name_for_gown(serial_number)
                    if worker_name:
                        if hasattr(self, 'worker_entry') and self.worker_entry.winfo_exists():
                             self.worker_entry.delete(0, tk.END)
                             self.worker_entry.insert(0, worker_name)
                             self.status_var.set(f"Tag already registered to {worker_name}")
                             logger.debug(f"Found existing worker: {worker_name}")
                else:
                     logger.error("Worker serial entry widget does not exist or was destroyed!")

            elif self.scan_target == 'washing':
                logger.debug("Processing for 'washing' target.")
                if hasattr(self, 'wash_serial_entry') and self.wash_serial_entry.winfo_exists():
                    self.wash_serial_entry.delete(0, tk.END)
                    self.wash_serial_entry.insert(0, serial_number)
                    logger.debug(f"Updated washing serial entry with '{serial_number}'.")
                    if hasattr(self, 'wash_button') and self.wash_button.winfo_exists():
                        self.wash_button.focus_set()
                    self.status_var.set(f"Tag scanned: {serial_number}. Press 'Record Washing'.")
                else:
                    logger.error("Washing serial entry widget does not exist or was destroyed!")
                    self.status_var.set(f"Tag scanned: {serial_number}. Error: Input field missing.")
            else:
                 logger.warning(f"Unknown scan target: {self.scan_target}")

        except tk.TclError as e:
             logger.error(f"TclError updating UI for scanned tag (window likely closing): {e}")
        except Exception as e:
             logger.error(f"Unexpected error processing scanned tag UI update: {e}", exc_info=True)

    def stop_rfid_scanning(self):
        """Stop RFID scanning"""
        if not self.scanning_active:
            return

        self.scanning_active = False

        if self.rfid_reader:
            if self.rfid_reader.running:
                 logger.debug("Stopping RFID reader from Worker Management.")
                 self.rfid_reader.stop()
            else:
                 logger.debug("RFID reader was not running, no need to stop.")
        else:
             logger.debug("No RFID reader instance to stop.")

        self.scan_target = None

        def reset_buttons():
            if self.window.winfo_exists():
                try:
                    if hasattr(self, 'scan_worker_button') and self.scan_worker_button.winfo_exists():
                        self.scan_worker_button.config(text="Scan RFID", command=self.scan_rfid)
                    if hasattr(self, 'scan_washing_button') and self.scan_washing_button.winfo_exists():
                        self.scan_washing_button.config(text="Scan RFID", command=self.scan_rfid_for_washing)
                except tk.TclError:
                     logger.warning("Error resetting scan buttons - window likely destroyed.")
                except Exception as e:
                     logger.error(f"Unexpected error resetting scan buttons: {e}", exc_info=True)

        if self.window.winfo_exists():
            self.window.after(0, reset_buttons)

        logger.info("RFID scanning stopped in Worker Management")

    def show_worker_menu(self, event):
        """Show context menu for worker table"""
        item = self.worker_table.identify_row(event.y)
        if item:
            if item not in self.worker_table.selection():
                 self.worker_table.selection_set(item)
            self.worker_menu.post(event.x_root, event.y_root)

    def edit_worker_name(self):
        """Edit the worker name for selected association"""
        selected = self.worker_table.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a worker entry to edit.", parent=self.window)
            return

        values = self.worker_table.item(selected[0], "values")
        if not values: return
        serial = values[0]
        current_name = values[1]

        new_name = simpledialog.askstring("Edit Worker Name",
                                         f"Enter new name for {serial}:",
                                         initialvalue=current_name, parent=self.window)

        if new_name and new_name.strip() and new_name.strip() != current_name:
            try:
                from config import sanitize_worker_name
                validated_name = sanitize_worker_name(new_name.strip())
                
                if self.db.add_worker_gown(serial, validated_name):
                    messagebox.showinfo("Success", f"Worker name updated for {serial}", parent=self.window)
                    self.refresh_data()
                else:
                    messagebox.showerror("Error", "Failed to update worker name", parent=self.window)
            except Exception as e:
                logger.error(f"Error updating worker name: {e}", exc_info=True)
                messagebox.showerror("Error", f"An error occurred: {str(e)}", parent=self.window)

    def delete_worker_gown(self):
        """Delete selected worker-gown association"""
        selected = self.worker_table.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a worker entry to delete.", parent=self.window)
            return

        values = self.worker_table.item(selected[0], "values")
        if not values: return
        serial = values[0]
        worker = values[1]

        if messagebox.askyesno("Confirm Delete",
                              f"Are you sure you want to delete the association for {serial} ({worker})?", parent=self.window):
            try:
                if self.db.delete_worker_gown(serial):
                    messagebox.showinfo("Success", f"Association for {serial} deleted", parent=self.window)
                    self.refresh_data()
                else:
                    messagebox.showerror("Error", "Failed to delete association", parent=self.window)
            except Exception as e:
                logger.error(f"Error deleting worker-gown: {e}", exc_info=True)
                messagebox.showerror("Error", f"An error occurred: {str(e)}", parent=self.window)

    def record_washing(self):
        """Record washing for selected gown from the worker tab"""
        selected = self.worker_table.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a worker entry to record washing.", parent=self.window)
            return

        values = self.worker_table.item(selected[0], "values")
        if not values: return
        serial = values[0]
        worker = values[1]

        try:
            if messagebox.askyesno("Confirm", f"Record washing for Gown #{serial} ({worker})?", parent=self.window):
                self.wash_serial_entry.delete(0, tk.END)
                self.wash_serial_entry.insert(0, serial)
                self.tab_control.select(self.washing_tab)
                self.add_washing_record()

        except Exception as e:
            logger.error(f"Error adding washing record from context menu: {e}", exc_info=True)
            messagebox.showerror("Error", f"An error occurred: {str(e)}", parent=self.window)

    def reset_first_use(self):
        """Reset the first use date for the selected gown"""
        selected = self.worker_table.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a worker entry to reset.", parent=self.window)
            return

        values = self.worker_table.item(selected[0], "values")
        if not values: return
        serial = values[0]
        worker = values[1]

        if messagebox.askyesno("Confirm Reset",
                              f"Reset 'First Use Date' for Gown #{serial} ({worker})?\n"
                              "This allows the 7-day lifetime to restart on next successful use.", parent=self.window):
            try:
                if self.db.reset_gown_first_use(serial):
                    messagebox.showinfo("Success", f"'First Use Date' reset for {serial}", parent=self.window)
                    self.refresh_data()
                else:
                    messagebox.showerror("Error", f"Failed to reset 'First Use Date' for {serial}", parent=self.window)
            except Exception as e:
                logger.error(f"Error resetting first use date: {e}", exc_info=True)
                messagebox.showerror("Error", f"An error occurred: {str(e)}", parent=self.window)

    def refresh_data(self):
        """Refresh all data in the tables"""
        logger.debug("Refreshing worker management data")
        self.refresh_worker_table()
        self.refresh_washing_table()
        if self.window.winfo_exists():
            self.status_var.set("Data refreshed")

    def refresh_worker_table(self):
        """Refresh the worker-gown table"""
        for item in self.worker_table.get_children():
            self.worker_table.delete(item)

        try:
            records = self.db.get_worker_gowns_with_details()
            logger.debug(f"Fetched {len(records)} worker gown details.")

            for i, record in enumerate(records):
                tag = 'evenrow' if i % 2 == 0 else 'oddrow'
                self.worker_table.insert("", "end", values=record, tags=(tag,))

        except Exception as e:
            logger.error(f"Error refreshing worker table: {e}", exc_info=True)
            if self.window.winfo_exists():
                 messagebox.showerror("Error", f"An error occurred while refreshing worker data: {str(e)}", parent=self.window)
                 self.status_var.set("Error loading worker data")

    def refresh_washing_table(self):
        """Refresh the washing records table"""
        for item in self.washing_table.get_children():
            self.washing_table.delete(item)

        try:
            records = self.db.get_washing_records()
            logger.debug(f"Fetched {len(records)} washing records.")

            for i, record in enumerate(records):
                tag = 'evenrow' if i % 2 == 0 else 'oddrow'
                self.washing_table.insert("", "end", values=record, tags=(tag,))

        except Exception as e:
            logger.error(f"Error refreshing washing table: {e}", exc_info=True)
            if self.window.winfo_exists():
                 messagebox.showerror("Error", f"An error occurred while refreshing washing data: {str(e)}", parent=self.window)
                 self.status_var.set("Error loading washing data")

    def sort_treeview(self, treeview, col, reverse):
        """Sort treeview contents by column"""
        try:
            data = []
            for k in treeview.get_children(''):
                 value = treeview.set(k, col)
                 sort_key = value if value is not None else ""
                 data.append((sort_key, k))

            try:
                if col == 'serial':
                     data.sort(key=lambda t: int(t[0]) if isinstance(t[0], str) and t[0].isdigit() else float('inf'), reverse=reverse)
                else:
                     data.sort(key=lambda t: str(t[0]).lower(), reverse=reverse)
            except ValueError:
                 data.sort(key=lambda t: str(t[0]), reverse=reverse)

            for i, (val, k) in enumerate(data):
                treeview.move(k, '', i)
                tag = 'evenrow' if i % 2 == 0 else 'oddrow'
                treeview.item(k, tags=(tag,))

            treeview.heading(col, command=lambda: self.sort_treeview(treeview, col, not reverse))
        except Exception as e:
            logger.error(f"Error sorting treeview column {col}: {e}", exc_info=True)

    def on_search_changed(self, *args):
        """Filter table contents based on search text"""
        search_text = self.search_var.get().lower().strip()

        try:
            current_tab_index = self.tab_control.index(self.tab_control.select())
        except tk.TclError:
             current_tab_index = 0

        if current_tab_index == 0:
            self.filter_table(self.worker_table, search_text, [0, 1])
        elif current_tab_index == 1:
            self.filter_table(self.washing_table, search_text, [0, 2])

    def filter_table(self, table, search_text, columns_to_search):
        """Filter table based on search text and specified columns"""
        all_items_data = {item: table.item(item, 'values') for item in table.get_children('')}

        currently_visible = set(table.get_children(''))
        items_to_show = set()
        items_to_hide = set()

        for item, values in all_items_data.items():
            if not values: continue

            match = False
            if not search_text:
                match = True
            else:
                if any(search_text in str(values[col_idx]).lower() for col_idx in columns_to_search if col_idx < len(values)):
                    match = True

            if match:
                items_to_show.add(item)
            else:
                items_to_hide.add(item)

        items_to_detach = items_to_hide.intersection(currently_visible)
        if items_to_detach:
            table.detach(*items_to_detach)

        items_to_reattach = items_to_show.difference(currently_visible)
        if items_to_reattach:
            match_count_reattach = 0
            for item in items_to_reattach:
                 table.reattach(item, '', 'end')
                 tag = 'evenrow' if match_count_reattach % 2 == 0 else 'oddrow'
                 table.item(item, tags=(tag,))
                 match_count_reattach += 1

        match_count = len(items_to_show)
        total_count = len(all_items_data)
        if self.window.winfo_exists():
            self.status_var.set(f"Showing {match_count} / {total_count} records")

    def backup_database(self):
        """Backup the database"""
        if hasattr(self.db, 'export_offline_data') and self.db.offline_mode:
            from tkinter import filedialog
            filepath = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
                title="Save Database Backup",
                parent=self.window
            )
            if filepath:
                if self.db.export_offline_data(filepath):
                    messagebox.showinfo("Success", f"Database backup saved to:\n{filepath}", parent=self.window)
                else:
                    messagebox.showerror("Error", "Failed to create database backup", parent=self.window)
        else:
            messagebox.showinfo("Information", "Database backup is only available when running in offline mode.", parent=self.window)

    def restore_database(self):
        """Restore the database from backup"""
        if hasattr(self.db, 'import_offline_data') and self.db.offline_mode:
            if messagebox.askyesno("Warning", "This will overwrite current data. Continue?", parent=self.window):
                from tkinter import filedialog
                filepath = filedialog.askopenfilename(
                    filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
                    title="Open Database Backup",
                    parent=self.window
                )
                if filepath:
                    if self.db.import_offline_data(filepath):
                        messagebox.showinfo("Success", f"Database restored successfully from:\n{filepath}", parent=self.window)
                        self.refresh_data()
                    else:
                        messagebox.showerror("Error", "Failed to restore database. Check file format.", parent=self.window)
        else:
            messagebox.showinfo("Information", "Database restore is only available when running in offline mode.", parent=self.window)

    def view_logs(self):
        """View system logs"""
        log_window = tk.Toplevel(self.window)
        log_window.title("System Logs")
        log_window.geometry("700x500")
        log_window.configure(bg=self.colors['bg_primary'])

        log_text_frame = tk.Frame(log_window, bg=self.colors['bg_primary'])
        log_text_frame.pack(fill=tk.BOTH, expand=True, pady=15, padx=15)

        log_text = tk.Text(log_text_frame, wrap=tk.NONE, height=20, width=80,
                           background=self.colors['card_bg'], 
                           foreground=self.colors['text_primary'], 
                           relief='flat', borderwidth=1,
                           font=('SF Pro Display', 10) if self._font_exists('SF Pro Display') else ('Consolas', 10))

        y_scrollbar = ttk.Scrollbar(log_text_frame, orient=tk.VERTICAL, command=log_text.yview)
        log_text.configure(yscrollcommand=y_scrollbar.set)

        x_scrollbar = ttk.Scrollbar(log_text_frame, orient=tk.HORIZONTAL, command=log_text.xview)
        log_text.configure(xscrollcommand=x_scrollbar.set)

        y_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        x_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        try:
            log_file = CONFIG.get('system', {}).get('log_file', 'cleanroom_app.log')
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    log_content = f.read()
                log_text.insert(tk.END, log_content)
                log_text.see(tk.END)
            else:
                log_text.insert(tk.END, f"Log file not found: {log_file}")
        except Exception as e:
            log_text.insert(tk.END, f"Error loading log file: {str(e)}")

        log_text.config(state=tk.DISABLED)

    def update_config(self, key_path, value):
        """Update configuration value (IN MEMORY ONLY for now)"""
        try:
            parts = key_path.split('.')
            current = CONFIG
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = value

            messagebox.showinfo("Config Update", f"Configuration updated (in memory):\n{key_path} = {value}\n\nRestart application for changes to take full effect.", parent=self.window)
            logger.info(f"Configuration updated in memory: {key_path} = {value}")

            if key_path == 'rfid.enable_mock':
                if self.rfid_reader:
                    self.rfid_reader.mock_mode = value
                    logger.info(f"RFID reader mock mode set to {value}")
                if hasattr(self, 'mock_rfid_var'):
                    self.mock_rfid_var.set(value)
                messagebox.showinfo("Info", "RFID mock mode updated.", parent=self.window)

        except Exception as e:
            logger.error(f"Error updating configuration: {e}", exc_info=True)
            messagebox.showerror("Error", f"Failed to update configuration: {str(e)}", parent=self.window)

    def on_closing(self):
        """Handle window closing"""
        logger.info("Closing Worker Management window")
        # Stop RFID reader if active
        self.stop_rfid_scanning()

        # Explicitly close the reader instance created by this window
        if self.rfid_reader:
            try:
                # Check if it was actually started before stopping
                if self.rfid_reader.running:
                     self.rfid_reader.stop()
                logger.info("RFID reader closed by Worker Management")
            except Exception as e:
                logger.error(f"Error closing RFID reader from Worker Management: {e}", exc_info=True)
            finally:
                 self.rfid_reader = None # Release reference

        self.window.destroy()


# Test function (kept for standalone testing if needed)
def main():
    # For testing only
    from firebase_database import FirebaseDatabase
    import logging

    # Configure logging
    logging.basicConfig(level=logging.INFO,
                       format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                       handlers=[
                           logging.FileHandler("cleanroom_app.log"),
                           logging.StreamHandler()
                       ])

    root = tk.Tk()
    # Don't withdraw root if testing standalone worker management
    # root.withdraw()

    db = FirebaseDatabase()
    # Ensure sample data exists if needed for testing
    if not db.get_worker_gowns():
        db._create_sample_data()

    # Show the worker management window attached to root
    app = WorkerManagementGUI(root, db)
    root.mainloop()


if __name__ == "__main__":
    main()