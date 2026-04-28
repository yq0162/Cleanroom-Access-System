# --- START OF FILE gui.py ---

import tkinter as tk
from tkinter import ttk, messagebox
import cv2
from PIL import Image, ImageTk, ImageFilter, ImageDraw
import threading
import time
import logging
import os
import math
from config import CONFIG
import numpy as np

logger = logging.getLogger(__name__)

class CleanroomGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Cleanroom Access Control System")

        # Get dimensions from config
        gui_config = CONFIG.get('gui', {})
        window_size = gui_config.get('window_size', '1000x700')
        self.max_video_width = gui_config.get('max_video_width', 640)
        self.max_video_height = gui_config.get('max_video_height', 480)

        self.root.geometry(window_size)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.minsize(720, 1000)  # Updated for portrait orientation
        
        # Configure for Raspberry Pi touch display (720x1280 portrait)
        self.root.attributes('-fullscreen',False)  # Enable fullscreen for touch display
        
        # Add keyboard shortcuts for development/debugging
        self.root.bind('<Escape>', self.toggle_fullscreen)  # ESC to toggle fullscreen
        self.root.bind('<F11>', self.toggle_fullscreen)     # F11 to toggle fullscreen
        self.root.bind('<Control-q>', self.force_quit)      # Ctrl+Q to force quit

        # Thread safety flag
        self._destroyed = False
        self._widget_lock = threading.RLock()
        
        # Current screen tracking (no animations)
        self._current_screen = None

        # Video update interval for frame rate control (milliseconds)
        self._video_update_interval = 16  # Default ~60 FPS (can be adjusted for stability)

        # --- APPLE-INSPIRED THEME SETUP ---
        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use('clam')
        except tk.TclError:
            logger.warning("Clam theme not available, using default.")
            try:
                self.style.theme_use('default')
            except tk.TclError as e:
                logger.error(f"Failed to set any theme: {e}")

        # Apple-inspired color palette
        self.colors = {
            # Primary colors
            'bg_primary': '#F2F2F7',        # Light gray background
            'bg_secondary': '#FFFFFF',       # Pure white
            'bg_tertiary': '#F8F9FA',       # Slightly off-white
            
            # Card and surface colors
            'card_bg': '#FFFFFF',           # White cards
            'card_shadow': '#E5E5EA',       # Subtle shadow
            'surface_elevated': '#FEFEFE',   # Elevated surfaces
            
            # Text colors
            'text_primary': '#1D1D1F',      # Near black
            'text_secondary': '#86868B',     # Gray
            'text_tertiary': '#C7C7CC',     # Light gray
            
            # Accent colors
            'accent_blue': '#007AFF',       # iOS blue
            'accent_green': '#30D158',      # iOS green  
            'accent_orange': '#FF9500',     # iOS orange
            'accent_red': '#FF3B30',        # iOS red
            'accent_purple': '#AF52DE',     # iOS purple
            
            # System colors
            'success': '#30D158',
            'warning': '#FF9500', 
            'error': '#FF3B30',
            'info': '#007AFF',
            
            # Hover states
            'hover_light': '#F0F0F5',
            'hover_medium': '#E8E8ED',
            'pressed': '#D1D1D6',
            
            # Gradients
            'gradient_start': '#F8F9FA',
            'gradient_end': '#E9ECEF'
        }

        # Configure root window
        self.root.configure(bg=self.colors['bg_primary'])
        
        # Setup sophisticated styling
        self._setup_premium_styles()

        # Initialize StringVar variables BEFORE setting up screens
        self.status_var = tk.StringVar()
        self.status_var.set("Ready")

        self.worker_var = tk.StringVar()
        self.worker_var.set("")

        self.serial_var = tk.StringVar()
        self.serial_var.set("")

        # Variables for tracking state
        self.current_frame = None
        self.video_running = False
        self.video_thread = None
        self.display_image = None
        self._image_references = []
        self._max_image_cache = 5

        # Face registration state
        self.face_reg_source = 'main'  # Track where face registration was accessed from ('main' or 'handwash')

        # Callbacks
        self.start_callback = None
        self.record_callback = None
        self.exit_callback = None
        self.manage_callback = None
        self.cancel_callback = None

        # Database reference
        self.db = None

        # Initialize widget references
        self.main_frame = None
        self.processing_frame = None
        self.record_frame = None
        self.video_label = None
        self.status_label = None
        self.progress_bar = None
        
        # Initialize icons placeholder
        self.icons = {}

        # Now set up the screens (without animations)
        try:
            self._setup_main_container()
            # Create icons safely without conflicts
            self.icons = self._create_premium_icons()
            self.setup_main_screen()
            self.setup_processing_screen()
            self.setup_record_screen()
            self.setup_handwash_screen()
            self.setup_face_registration_screen()
            # Show main screen immediately without animation
            self._show_screen_immediate('main')
        except Exception as e:
            logger.error(f"Error setting up GUI screens: {e}", exc_info=True)
            raise

        logger.info("Premium GUI initialized with no-animation design for optimal performance")

    def _setup_premium_styles(self):
        """Setup sophisticated Apple-inspired styling"""
        try:
            # Base styles
            self.style.configure('.', 
                background=self.colors['bg_primary'], 
                foreground=self.colors['text_primary'], 
                font=('SF Pro Display', 10) if self._font_exists('SF Pro Display') else ('Segoe UI', 10))
            
            # Frame styles
            self.style.configure('TFrame', background=self.colors['bg_primary'], borderwidth=0)
            self.style.configure('Card.TFrame', 
                background=self.colors['card_bg'], 
                relief='flat', 
                borderwidth=1,
                padding=20)
            self.style.configure('Surface.TFrame', 
                background=self.colors['surface_elevated'],
                relief='flat',
                borderwidth=0)
            
            # Label styles  
            self.style.configure('TLabel', 
                background=self.colors['bg_primary'], 
                foreground=self.colors['text_primary'],
                font=('SF Pro Display', 10) if self._font_exists('SF Pro Display') else ('Segoe UI', 10))
            
            self.style.configure('Title.TLabel', 
                font=('SF Pro Display', 32, 'normal') if self._font_exists('SF Pro Display') else ('Segoe UI', 32, 'normal'), 
                background=self.colors['bg_primary'], 
                foreground=self.colors['text_primary'])
                
            self.style.configure('Subtitle.TLabel',
                font=('SF Pro Display', 18, 'normal') if self._font_exists('SF Pro Display') else ('Segoe UI', 18, 'normal'),
                background=self.colors['bg_primary'],
                foreground=self.colors['text_secondary'])
                
            self.style.configure('Info.TLabel', 
                font=('SF Pro Display', 14, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 14, 'bold'), 
                background=self.colors['card_bg'],
                foreground=self.colors['text_primary'])
                
            self.style.configure('Muted.TLabel', 
                foreground=self.colors['text_secondary'], 
                background=self.colors['card_bg'],
                font=('SF Pro Display', 11) if self._font_exists('SF Pro Display') else ('Segoe UI', 11))

            # Premium button styles
            self._setup_button_styles()
            
            # Table styles
            self._setup_table_styles()
            
        except Exception as e:
            logger.error(f"Error configuring premium theme: {e}", exc_info=True)

    def _setup_button_styles(self):
        """Setup premium button styling with hover effects"""
        # Primary action button (blue)
        self.style.configure('Primary.TButton',
            background=self.colors['accent_blue'],
            foreground='white',
            font=('SF Pro Display', 14, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 14, 'bold'),
            padding=(20, 12),
            relief='flat',
            borderwidth=0)
        self.style.map('Primary.TButton',
            background=[('active', '#0066CC'), ('pressed', '#004499')])
            
        # Success button (green)  
        self.style.configure('Success.TButton',
            background=self.colors['success'],
            foreground='white', 
            font=('SF Pro Display', 16, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 16, 'bold'),
            padding=(24, 14),
            relief='flat',
            borderwidth=0)
        self.style.map('Success.TButton',
            background=[('active', '#28B946'), ('pressed', '#20A93E')])
            
        # Warning button (orange)
        self.style.configure('Warning.TButton', 
            background=self.colors['warning'],
            foreground='white',
            font=('SF Pro Display', 14, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 14, 'bold'),
            padding=(20, 12),
            relief='flat',
            borderwidth=0)
        self.style.map('Warning.TButton',
            background=[('active', '#E6850E'), ('pressed', '#CC7A0D')])
            
        # Danger button (red)
        self.style.configure('Danger.TButton',
            background=self.colors['error'],
            foreground='white',
            font=('SF Pro Display', 14, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 14, 'bold'),
            padding=(20, 12), 
            relief='flat',
            borderwidth=0)
        self.style.map('Danger.TButton',
            background=[('active', '#E6342A'), ('pressed', '#CC2E25')])
            
        # Secondary button (gray)
        self.style.configure('Secondary.TButton',
            background=self.colors['hover_medium'],
            foreground=self.colors['text_primary'],
            font=('SF Pro Display', 13, 'normal') if self._font_exists('SF Pro Display') else ('Segoe UI', 13, 'normal'),
            padding=(16, 10),
            relief='flat', 
            borderwidth=0)
        self.style.map('Secondary.TButton',
            background=[('active', self.colors['hover_light']), ('pressed', self.colors['pressed'])])

        # Accent button (purple) for face registration
        self.style.configure('Accent.TButton',
            background=self.colors['accent_purple'],
            foreground='white',
            font=('SF Pro Display', 14, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 14, 'bold'),
            padding=(20, 12),
            relief='flat',
            borderwidth=0)
        self.style.map('Accent.TButton',
            background=[('active', '#9A45C7'), ('pressed', '#8A3BB7')])

        # Info button (blue) for hand washing
        self.style.configure('Info.TButton',
            background=self.colors['info'],
            foreground='white',
            font=('SF Pro Display', 14, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 14, 'bold'),
            padding=(20, 12),
            relief='flat',
            borderwidth=0)
        self.style.map('Info.TButton',
            background=[('active', '#0066CC'), ('pressed', '#004499')])

    def _setup_table_styles(self):
        """Setup premium table styling"""
        self.style.configure('Treeview.Heading', 
            font=('SF Pro Display', 12, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 12, 'bold'), 
            background=self.colors['bg_tertiary'], 
            foreground=self.colors['text_primary'],
            relief='flat',
            borderwidth=1)
        self.style.map('Treeview.Heading', 
            relief=[('active','flat'),('pressed','flat')])
        self.style.configure('Treeview', 
            fieldbackground=self.colors['card_bg'], 
            background=self.colors['card_bg'],
            foreground=self.colors['text_primary'],
            borderwidth=0,
            rowheight=28)

    def _font_exists(self, font_name):
        """Check if a font exists on the system"""
        try:
            import tkinter.font as tkFont
            return font_name in tkFont.families()
        except:
            return False

    def _is_valid_widget(self, widget):
        """Check if a widget is still valid and exists"""
        if self._destroyed:
            return False
        try:
            return widget and widget.winfo_exists()
        except tk.TclError:
            return False

    def _safe_widget_operation(self, operation, *args, **kwargs):
        """Safely perform widget operations with error handling"""
        if self._destroyed:
            return None
        try:
            with self._widget_lock:
                if not self._destroyed:
                    return operation(*args, **kwargs)
        except tk.TclError as e:
            logger.warning(f"Widget operation failed (widget may be destroyed): {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in widget operation: {e}", exc_info=True)
            return None

    def _create_premium_icons(self):
        """Create premium-looking icons programmatically with better error handling"""
        icons = {}
        icon_size = 24
        
        try:
            # Clear any existing image references to prevent conflicts
            self._cleanup_image_references()
            
            # Create SF Symbols-inspired icons
            icons['start'] = self._create_play_icon(icon_size, self.colors['success'])
            icons['records'] = self._create_document_icon(icon_size, self.colors['accent_blue']) 
            icons['manage'] = self._create_person_icon(icon_size, self.colors['accent_orange'])
            icons['handwash'] = self._create_handwash_icon(icon_size, self.colors['accent_purple'])
            icons['exit'] = self._create_xmark_icon(icon_size, self.colors['error'])
            icons['back'] = self._create_chevron_icon(icon_size, self.colors['text_secondary'], 'left')
            icons['logo'] = self._create_app_logo(120)
            
            logger.info("Premium icons created successfully")
            
        except Exception as e:
            logger.warning(f"Failed to create premium icons: {e}")
            # Create empty icons as fallback
            for icon_name in ['start', 'records', 'manage', 'handwash', 'exit', 'back', 'logo']:
                icons[icon_name] = None
            
        return icons

    def _cleanup_image_references(self):
        """Clean up image references to prevent memory leaks and conflicts"""
        try:
            # Clear main image references
            if hasattr(self, 'display_image') and self.display_image:
                try:
                    del self.display_image
                except:
                    pass
                self.display_image = None
            
            # Clear image reference cache
            if hasattr(self, '_image_references'):
                old_refs = self._image_references.copy()
                self._image_references.clear()
                
                for old_ref in old_refs:
                    try:
                        del old_ref
                    except:
                        pass
            
            # Clear icon references if they exist
            if hasattr(self, 'icons') and self.icons:
                old_icons = self.icons.copy()
                self.icons.clear()
                
                for icon_name, icon_obj in old_icons.items():
                    if icon_obj:
                        try:
                            del icon_obj
                        except:
                            pass
            
            # Force garbage collection
            import gc
            gc.collect()
            
        except Exception as e:
            logger.debug(f"Error during image cleanup: {e}")

    def _create_play_icon(self, size, color):
        """Create a play button icon"""
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Draw play triangle
        triangle = [
            (size//4, size//6),
            (size//4, size*5//6), 
            (size*3//4, size//2)
        ]
        draw.polygon(triangle, fill=color)
        
        return ImageTk.PhotoImage(img)

    def _create_document_icon(self, size, color):
        """Create a document icon"""
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Draw document shape
        margin = size // 6
        draw.rectangle([margin, margin, size-margin, size-margin], 
                      outline=color, fill=None, width=2)
        
        # Draw lines
        for i in range(3):
            y = margin + (i+1) * (size-2*margin) // 4
            draw.line([margin + size//6, y, size-margin-size//6, y], fill=color, width=1)
            
        return ImageTk.PhotoImage(img)

    def _create_person_icon(self, size, color):
        """Create a person icon"""
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        center_x, center_y = size // 2, size // 2
        
        # Draw head (circle)
        head_radius = size // 6
        draw.ellipse([center_x - head_radius, center_y - size//3,
                     center_x + head_radius, center_y - size//3 + 2*head_radius],
                    outline=color, width=2)
        
        # Draw body (arc)
        body_radius = size // 3
        draw.arc([center_x - body_radius, center_y - size//6,
                 center_x + body_radius, center_y + size//3],
                start=0, end=180, fill=color, width=2)
                
        return ImageTk.PhotoImage(img)

    def _create_xmark_icon(self, size, color):
        """Create an X mark icon"""
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        margin = size // 4
        draw.line([margin, margin, size-margin, size-margin], fill=color, width=2)
        draw.line([margin, size-margin, size-margin, margin], fill=color, width=2)
        
        return ImageTk.PhotoImage(img)

    def _create_handwash_icon(self, size, color):
        """Create a hand washing icon"""
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Draw hands (two oval shapes)
        hand_width = size // 3
        hand_height = size // 4
        
        # Left hand
        left_x = size // 6
        left_y = size // 2
        draw.ellipse([left_x, left_y, left_x + hand_width, left_y + hand_height], 
                    outline=color, width=2)
        
        # Right hand (slightly offset)
        right_x = size // 2
        right_y = size // 3
        draw.ellipse([right_x, right_y, right_x + hand_width, right_y + hand_height], 
                    outline=color, width=2)
        
        # Draw water drops
        for i in range(3):
            drop_x = size // 4 + i * size // 6
            drop_y = size // 8
            drop_size = size // 12
            draw.ellipse([drop_x, drop_y, drop_x + drop_size, drop_y + drop_size*2], 
                        fill=color)
        
        return ImageTk.PhotoImage(img)

    def _create_chevron_icon(self, size, color, direction='left'):
        """Create a chevron icon"""
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        center_x, center_y = size // 2, size // 2
        arrow_size = size // 3
        
        if direction == 'left':
            points = [
                (center_x + arrow_size//2, center_y - arrow_size),
                (center_x - arrow_size//2, center_y),
                (center_x + arrow_size//2, center_y + arrow_size)
            ]
        else:  # right
            points = [
                (center_x - arrow_size//2, center_y - arrow_size),
                (center_x + arrow_size//2, center_y),
                (center_x - arrow_size//2, center_y + arrow_size)
            ]
        
        draw.line(points[:2], fill=color, width=2)
        draw.line(points[1:], fill=color, width=2)
        
        return ImageTk.PhotoImage(img)

    def _create_app_logo(self, size):
        """Create app logo with clean, modern design"""
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        center = size // 2
        
        # Create gradient circle background
        for i in range(size//2):
            alpha = int(255 * (1 - i / (size//2)) * 0.1)
            color = (*self._hex_to_rgb(self.colors['accent_blue']), alpha)
            draw.ellipse([center-i, center-i, center+i, center+i], 
                        fill=color)
        
        # Main circle
        margin = size // 8
        draw.ellipse([margin, margin, size-margin, size-margin],
                    fill=self.colors['accent_blue'])
        
        # Inner shield shape
        inner_margin = size // 4
        shield_points = [
            (center, inner_margin + size//8),
            (size - inner_margin - size//8, inner_margin + size//4),
            (size - inner_margin - size//8, size - inner_margin - size//4),
            (center, size - inner_margin),
            (inner_margin + size//8, size - inner_margin - size//4),
            (inner_margin + size//8, inner_margin + size//4)
        ]
        draw.polygon(shield_points, fill='white')
        
        return ImageTk.PhotoImage(img)

    def _hex_to_rgb(self, hex_color):
        """Convert hex color to RGB tuple"""
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

    def _setup_main_container(self):
        """Setup main container for screen transitions"""
        self.main_container = tk.Frame(self.root, bg=self.colors['bg_primary'])
        self.main_container.pack(fill=tk.BOTH, expand=True)

    def setup_main_screen(self):
        """Set up the main menu screen with premium design"""
        try:
            self.main_frame = tk.Frame(self.main_container, bg=self.colors['bg_primary'])
            
            # Main content container with subtle shadow effect
            content_frame = tk.Frame(self.main_frame, bg=self.colors['bg_primary'])
            content_frame.pack(expand=True, fill=tk.BOTH, padx=60, pady=40)
            
            # Title section with elegant spacing
            title_container = tk.Frame(content_frame, bg=self.colors['bg_primary'])
            title_container.pack(pady=(0, 50))
            
            # App logo
            if "logo" in self.icons:
                logo_label = tk.Label(title_container, image=self.icons["logo"], 
                                    bg=self.colors['bg_primary'])
                logo_label.pack(pady=(0, 20))
                logo_label.image = self.icons["logo"]
            
            # Main title with premium typography
            title_label = tk.Label(title_container, 
                                 text="Cleanroom Access Control",
                                 font=('SF Pro Display', 34, 'normal') if self._font_exists('SF Pro Display') else ('Segoe UI', 34, 'normal'),
                                 fg=self.colors['text_primary'],
                                 bg=self.colors['bg_primary'])
            title_label.pack()
            
            # Subtitle
            subtitle_label = tk.Label(title_container,
                                    text="Advanced PPE Compliance System",
                                    font=('SF Pro Display', 16, 'normal') if self._font_exists('SF Pro Display') else ('Segoe UI', 16, 'normal'),
                                    fg=self.colors['text_secondary'],
                                    bg=self.colors['bg_primary'])
            subtitle_label.pack(pady=(8, 0))

            # Buttons container with card-like appearance
            buttons_container = tk.Frame(content_frame, bg=self.colors['card_bg'], 
                                        relief='flat', bd=0)
            buttons_container.pack(pady=30)
            
            # Add subtle shadow effect
            shadow_frame = tk.Frame(content_frame, bg=self.colors['card_shadow'], height=2)
            shadow_frame.pack(fill=tk.X, pady=(28, 0))
            
            buttons_frame = tk.Frame(buttons_container, bg=self.colors['card_bg'])
            buttons_frame.pack(padx=40, pady=40)

            # Create premium buttons with animations
            self.start_btn = self._create_premium_button(
                buttons_frame, "Start New Scan", self.on_start_clicked,
                style='Success.TButton', icon=self.icons.get("start"))
            self.start_btn.pack(pady=(0, 16), fill=tk.X, ipady=8)

            self.record_btn = self._create_premium_button(
                buttons_frame, "View System Records", self.on_record_clicked,
                style='Primary.TButton', icon=self.icons.get("records"))
            self.record_btn.pack(pady=8, fill=tk.X, ipady=4)

            self.manage_btn = self._create_premium_button(
                buttons_frame, "Manage Workers & Gowns", self.on_manage_clicked,
                style='Warning.TButton', icon=self.icons.get("manage"))
            self.manage_btn.pack(pady=8, fill=tk.X, ipady=4)

            self.handwash_btn = self._create_premium_button(
                buttons_frame, "Hand Washing Monitor", self.on_handwash_clicked,
                style='Info.TButton', icon=self.icons.get("handwash"))
            self.handwash_btn.pack(pady=8, fill=tk.X, ipady=4)

            # Add face registration button to main menu
            self.face_reg_btn = self._create_premium_button(
                buttons_frame, "Register Worker Face", self.on_main_face_reg_clicked,
                style='Accent.TButton', icon=self.icons.get("manage"))
            self.face_reg_btn.pack(pady=8, fill=tk.X, ipady=4)

            self.exit_btn = self._create_premium_button(
                buttons_frame, "Exit Application", self.on_exit_clicked,
                style='Danger.TButton', icon=self.icons.get("exit"))
            self.exit_btn.pack(pady=(16, 0), fill=tk.X, ipady=4)

        except Exception as e:
            logger.error(f"Error setting up main screen: {e}", exc_info=True)
            raise

    def _create_premium_button(self, parent, text, command, style='Primary.TButton', icon=None):
        """Create a premium styled button with optional icon (no hover animations)"""
        btn = ttk.Button(parent, text=text, command=command, style=style)
        if icon:
            btn.configure(image=icon, compound=tk.LEFT)
        return btn

    def _create_table_button(self, parent, text, command):
        """Create a table selection button with consistent styling (no hover animations)"""
        btn = ttk.Button(parent, text=text, command=command, style='Secondary.TButton')
        return btn

    def setup_processing_screen(self):
        """Set up the processing screen with modern design"""
        try:
            self.processing_frame = tk.Frame(self.main_container, bg=self.colors['bg_primary'])

            # Main container with premium spacing
            main_container = tk.Frame(self.processing_frame, bg=self.colors['bg_primary'])
            main_container.pack(fill=tk.BOTH, expand=True, padx=30, pady=30)

            # Top information card
            info_card = tk.Frame(main_container, bg=self.colors['card_bg'], relief='flat', bd=0)
            info_card.pack(fill=tk.X, pady=(0, 20))
            
            # Add subtle shadow
            shadow = tk.Frame(main_container, bg=self.colors['card_shadow'], height=1)
            shadow.pack(fill=tk.X, pady=(18, 20))

            info_inner = tk.Frame(info_card, bg=self.colors['card_bg'])
            info_inner.pack(padx=25, pady=20)

            # Configure grid for info layout
            info_inner.grid_columnconfigure(0, weight=1)
            info_inner.grid_columnconfigure(1, weight=1)

            # Worker information with premium styling
            worker_container = tk.Frame(info_inner, bg=self.colors['card_bg'])
            worker_container.grid(row=0, column=0, sticky='ew', padx=(0, 15))
            
            tk.Label(worker_container, text="Worker Name",
                    font=('SF Pro Display', 11, 'normal') if self._font_exists('SF Pro Display') else ('Segoe UI', 11, 'normal'),
                    fg=self.colors['text_secondary'], bg=self.colors['card_bg']).pack(anchor='w')
            
            worker_value = tk.Label(worker_container, textvariable=self.worker_var,
                                  font=('SF Pro Display', 16, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 16, 'bold'),
                                  fg=self.colors['text_primary'], bg=self.colors['card_bg'])
            worker_value.pack(anchor='w', pady=(4, 0))

            # Gown ID information
            gown_container = tk.Frame(info_inner, bg=self.colors['card_bg'])
            gown_container.grid(row=0, column=1, sticky='ew', padx=(15, 0))
            
            tk.Label(gown_container, text="Gown ID", 
                    font=('SF Pro Display', 11, 'normal') if self._font_exists('SF Pro Display') else ('Segoe UI', 11, 'normal'),
                    fg=self.colors['text_secondary'], bg=self.colors['card_bg']).pack(anchor='w')
            
            gown_value = tk.Label(gown_container, textvariable=self.serial_var,
                                font=('SF Pro Display', 16, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 16, 'bold'),
                                fg=self.colors['text_primary'], bg=self.colors['card_bg'])
            gown_value.pack(anchor='w', pady=(4, 0))

            # Video container with modern design
            video_container = tk.Frame(main_container, bg=self.colors['card_bg'], relief='flat', bd=0)
            video_container.pack(fill=tk.BOTH, expand=True, pady=(0, 20))
            
            # Video header
            video_header = tk.Frame(video_container, bg=self.colors['card_bg'])
            video_header.pack(fill=tk.X, padx=20, pady=(20, 10))
            
            tk.Label(video_header, text="Live Camera Feed",
                    font=('SF Pro Display', 18, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 18, 'bold'),
                    fg=self.colors['text_primary'], bg=self.colors['card_bg']).pack(side=tk.LEFT)

            # Video display area with rounded appearance
            video_frame = tk.Frame(video_container, bg=self.colors['bg_primary'], relief='flat', bd=0)
            video_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))

            self.video_label = tk.Label(video_frame, background='#000000', 
                                      fg='white', anchor=tk.CENTER,
                                      text="Initializing Camera...",
                                      font=('SF Pro Display', 14, 'normal') if self._font_exists('SF Pro Display') else ('Segoe UI', 14, 'normal'))
            self.video_label.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

            # Status display with dynamic styling
            self.status_frame = tk.Frame(main_container, height=70, relief='flat', bd=0)
            self.status_frame.pack(fill=tk.X, pady=(0, 20))
            self.status_frame.pack_propagate(False)

            self.status_label = tk.Label(self.status_frame, textvariable=self.status_var,
                                        font=('SF Pro Display', 20, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 20, 'bold'),
                                        bg=self.colors['info'], fg='white')
            self.status_label.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

            # Modern progress indicator
            progress_container = tk.Frame(main_container, bg=self.colors['bg_primary'])
            progress_container.pack(fill=tk.X, pady=(0, 20))
            
            self.progress_var = tk.DoubleVar()
            self.progress_bar = ttk.Progressbar(progress_container, 
                                              orient="horizontal",
                                              mode="indeterminate",
                                              variable=self.progress_var)
            self.progress_bar.pack(fill=tk.X, pady=2)
            self.progress_bar.pack_forget()  # Hidden initially

            # Bottom button bar with premium styling
            button_bar = tk.Frame(main_container, bg=self.colors['bg_primary'])
            button_bar.pack(fill=tk.X)

            # Back button with icon
            self.back_btn = ttk.Button(button_bar, text="  Back",
                                     command=self.show_main_screen,
                                     style='Secondary.TButton',
                                     image=self.icons.get("back"),
                                     compound=tk.LEFT)
            self.back_btn.pack(side=tk.LEFT)

            # Cancel button
            self.cancel_btn = ttk.Button(button_bar, text="Cancel Scan",
                                       command=self.on_cancel_clicked,
                                       style='Danger.TButton')
            self.cancel_btn.pack(side=tk.RIGHT)

        except Exception as e:
            logger.error(f"Error setting up processing screen: {e}", exc_info=True)
            raise

    def setup_record_screen(self):
        """Set up the record viewing screen with modern table design"""
        try:
            self.record_frame = tk.Frame(self.main_container, bg=self.colors['bg_primary'])

            # Header with elegant title
            header_container = tk.Frame(self.record_frame, bg=self.colors['bg_primary'])
            header_container.pack(fill=tk.X, padx=30, pady=(30, 20))

            title_label = tk.Label(header_container, text="System Records",
                                 font=('SF Pro Display', 28, '600') if self._font_exists('SF Pro Display') else ('Segoe UI', 28, 'bold'),
                                 fg=self.colors['text_primary'], bg=self.colors['bg_primary'])
            title_label.pack(side=tk.LEFT)

            # Controls card
            controls_card = tk.Frame(self.record_frame, bg=self.colors['card_bg'], relief='flat', bd=0)
            controls_card.pack(fill=tk.X, padx=30, pady=(0, 20))

            controls_inner = tk.Frame(controls_card, bg=self.colors['card_bg'])
            controls_inner.pack(padx=25, pady=20)

            # Table selection with modern button group
            btn_group = tk.Frame(controls_inner, bg=self.colors['card_bg'])
            btn_group.pack(side=tk.LEFT, padx=(0, 20))

            self._create_table_button(btn_group, "Workers & Gowns", lambda: self.show_records_table(1)).pack(side=tk.LEFT, padx=(0, 5))
            self._create_table_button(btn_group, "Washing Records", lambda: self.show_records_table(2)).pack(side=tk.LEFT, padx=5)
            self._create_table_button(btn_group, "Compliance", lambda: self.show_records_table(3)).pack(side=tk.LEFT, padx=5)
            self._create_table_button(btn_group, "Hand Washing", lambda: self.show_records_table(4)).pack(side=tk.LEFT, padx=(5, 0))

            # Search area with modern styling
            search_container = tk.Frame(controls_inner, bg=self.colors['card_bg'])
            search_container.pack(side=tk.RIGHT)

            tk.Label(search_container, text="Search:",
                    font=('SF Pro Display', 12, 'normal') if self._font_exists('SF Pro Display') else ('Segoe UI', 12, 'normal'),
                    fg=self.colors['text_secondary'], bg=self.colors['card_bg']).pack(side=tk.LEFT, padx=(0, 8))

            self.record_search_var = tk.StringVar()
            self.record_search_var.trace_add("write", self.on_record_search)

            search_entry = tk.Entry(search_container, textvariable=self.record_search_var, 
                                  width=25, relief='flat', bd=1,
                                  font=('SF Pro Display', 11, 'normal') if self._font_exists('SF Pro Display') else ('Segoe UI', 11, 'normal'))
            search_entry.pack(side=tk.LEFT, padx=(0, 10), ipady=4)

            refresh_btn = ttk.Button(search_container, text="Refresh",
                                   command=self.refresh_current_table,
                                   style='Secondary.TButton')
            refresh_btn.pack(side=tk.LEFT)

            # Table container with modern styling
            self.table_frame = tk.Frame(self.record_frame, bg=self.colors['card_bg'], relief='flat', bd=0)
            self.table_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=(0, 20))

            self.current_table_id = 1

            # Back button with premium styling
            back_container = tk.Frame(self.record_frame, bg=self.colors['bg_primary'])
            back_container.pack(fill=tk.X, padx=30, pady=(0, 30))
            
            ttk.Button(back_container, text="  Back to Main Menu",
                     command=self.show_main_screen, style='Secondary.TButton',
                     image=self.icons.get("back"), compound=tk.LEFT).pack(side=tk.LEFT)

        except Exception as e:
            logger.error(f"Error setting up record screen: {e}", exc_info=True)
            raise

    def setup_handwash_screen(self):
        """Set up hand washing monitoring screen"""
        if hasattr(self, 'handwash_frame'):
            self.handwash_frame.destroy()
        
        self.handwash_frame = ttk.Frame(self.main_container)
        
        # Header
        header_frame = ttk.Frame(self.handwash_frame)
        header_frame.pack(fill='x', padx=20, pady=(20, 10))
        
        # Back button
        back_btn = ttk.Button(header_frame, text="← Back to Main", 
                             command=self.show_main_screen,
                             style='Secondary.TButton')
        back_btn.pack(side='left')
        
        # Title
        title_label = ttk.Label(header_frame, text="Hand Washing Monitor", 
                               font=('Segoe UI', 24, 'bold'),
                               foreground=self.colors['text_primary'])
        title_label.pack(side='left', padx=(20, 0))
        
        # Content area
        content_frame = ttk.Frame(self.handwash_frame)
        content_frame.pack(fill='both', expand=True, padx=20, pady=10)
        
        # Left side - Video feed
        video_frame = ttk.LabelFrame(content_frame, text="Camera Feed", padding="10")
        video_frame.pack(side='left', fill='both', expand=True, padx=(0, 10))
        
        # Video display area
        self.handwash_video_label = ttk.Label(video_frame, text="Camera feed will appear here", 
                                             background=self.colors['bg_secondary'])
        self.handwash_video_label.pack(fill='both', expand=True)
        
        # Right side - Controls and status
        control_frame = ttk.Frame(content_frame)
        control_frame.pack(side='right', fill='y', padx=(10, 0))
        
        # Status section
        status_section = ttk.LabelFrame(control_frame, text="Current Status", padding="10")
        status_section.pack(fill='x', pady=(0, 10))
        
        # Worker status
        ttk.Label(status_section, text="Worker:", font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        self.handwash_worker_label = ttk.Label(status_section, text="None Detected", 
                                              font=('Segoe UI', 12))
        self.handwash_worker_label.pack(anchor='w', pady=(0, 10))
        
        # Duration status
        ttk.Label(status_section, text="Duration:", font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        self.handwash_duration_label = ttk.Label(status_section, text="0.0s", 
                                                font=('Segoe UI', 12))
        self.handwash_duration_label.pack(anchor='w', pady=(0, 10))
        
        # Quality status
        ttk.Label(status_section, text="Quality:", font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        self.handwash_quality_label = ttk.Label(status_section, text="Not Washing", 
                                               font=('Segoe UI', 12))
        self.handwash_quality_label.pack(anchor='w')
        
        # Controls section
        controls_section = ttk.LabelFrame(control_frame, text="Controls", padding="10")
        controls_section.pack(fill='x', pady=(0, 10))
        
        # Start monitoring button
        self.handwash_start_btn = ttk.Button(controls_section, text="Start Monitoring",
                                           command=self.on_handwash_start_clicked,
                                           style='Primary.TButton')
        self.handwash_start_btn.pack(fill='x', pady=(0, 5))
        
        # Stop monitoring button
        self.handwash_stop_btn = ttk.Button(controls_section, text="Stop Monitoring",
                                          command=self.on_handwash_stop_clicked,
                                          style='Secondary.TButton',
                                          state='disabled')
        self.handwash_stop_btn.pack(fill='x', pady=(0, 10))
        
        # Register face button
        self.register_face_btn = ttk.Button(controls_section, text="Register Worker Face",
                                          command=self.on_register_face_clicked,
                                          style='Accent.TButton')
        self.register_face_btn.pack(fill='x')

    def setup_face_registration_screen(self):
        """Set up enhanced face registration screen"""
        if hasattr(self, 'face_registration_frame'):
            self.face_registration_frame.destroy()
        
        self.face_registration_frame = ttk.Frame(self.main_container)
        
        # Header
        header_frame = ttk.Frame(self.face_registration_frame)
        header_frame.pack(fill='x', padx=20, pady=(20, 10))
        
        # Back button - check if we came from handwash or main menu
        self.face_reg_back_btn = ttk.Button(header_frame, text="← Back", 
                             command=self.on_face_reg_back_clicked,
                             style='Secondary.TButton')
        self.face_reg_back_btn.pack(side='left')
        
        # Title
        title_label = ttk.Label(header_frame, text="Enhanced Face Registration", 
                               font=('Segoe UI', 24, 'bold'),
                               foreground=self.colors['text_primary'])
        title_label.pack(side='left', padx=(20, 0))
        
        # Instructions
        instructions_frame = ttk.Frame(self.face_registration_frame)
        instructions_frame.pack(fill='x', padx=20, pady=(0, 10))
        
        instructions = ttk.Label(instructions_frame, 
            text="This process will capture your face from multiple angles for better recognition accuracy.\n"
                 "Please follow the on-screen instructions and move your head slowly as guided.",
            font=('Segoe UI', 11), foreground=self.colors['accent_blue'], 
            wraplength=800, justify='center')
        instructions.pack()
        
        # Main content area
        content_frame = ttk.Frame(self.face_registration_frame)
        content_frame.pack(fill='both', expand=True, padx=20, pady=10)
        
        # Left side - Video feed
        video_frame = ttk.LabelFrame(content_frame, text="Live Camera Feed", padding="10")
        video_frame.pack(side='left', fill='both', expand=True, padx=(0, 10))
        
        # Video display area
        self.face_reg_video_label = ttk.Label(video_frame, text="Starting camera...", 
                                             background=self.colors['bg_secondary'])
        self.face_reg_video_label.pack(fill='both', expand=True)
        
        # Right side - Controls and progress
        control_frame = ttk.Frame(content_frame)
        control_frame.pack(side='right', fill='y', padx=(10, 0))
        
        # Worker information section
        info_section = ttk.LabelFrame(control_frame, text="Worker Information", padding="10")
        info_section.pack(fill='x', pady=(0, 10))
        
        ttk.Label(info_section, text="Worker Name:", font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        self.face_reg_name_var = tk.StringVar()
        name_entry = ttk.Entry(info_section, textvariable=self.face_reg_name_var, 
                              font=('Segoe UI', 12), width=25)
        name_entry.pack(fill='x', pady=(0, 10))
        
        # Progress section
        progress_section = ttk.LabelFrame(control_frame, text="Registration Progress", padding="10")
        progress_section.pack(fill='x', pady=(0, 10))
        
        # Progress bar
        self.face_reg_progress = ttk.Progressbar(progress_section, mode='determinate', length=250)
        self.face_reg_progress.pack(fill='x', pady=(0, 5))
        
        # Progress label
        self.face_reg_progress_var = tk.StringVar(value="Ready")
        progress_label = ttk.Label(progress_section, textvariable=self.face_reg_progress_var, 
                                  font=('Segoe UI', 12, 'bold'))
        progress_label.pack()
        
        # Status section
        status_section = ttk.LabelFrame(control_frame, text="Current Status", padding="10")
        status_section.pack(fill='x', pady=(0, 10))
        
        self.face_reg_status_var = tk.StringVar(value="Enter worker name and click 'Start Registration'")
        status_label = ttk.Label(status_section, textvariable=self.face_reg_status_var, 
                                font=('Segoe UI', 10), foreground=self.colors['accent_blue'],
                                wraplength=250, justify='left')
        status_label.pack()
        
        # Controls section
        controls_section = ttk.LabelFrame(control_frame, text="Controls", padding="10")
        controls_section.pack(fill='x')
        
        # Start registration button
        self.face_reg_start_btn = ttk.Button(controls_section, text="Start Registration",
                                           command=self.on_face_reg_start_clicked,
                                           style='Primary.TButton')
        self.face_reg_start_btn.pack(fill='x', pady=(0, 5))
        
        # Stop registration button
        self.face_reg_stop_btn = ttk.Button(controls_section, text="Stop Registration",
                                          command=self.on_face_reg_stop_clicked,
                                          style='Secondary.TButton',
                                          state='disabled')
        self.face_reg_stop_btn.pack(fill='x', pady=(0, 10))
        
        # Cancel button
        cancel_btn = ttk.Button(controls_section, text="Cancel",
                               command=self.on_face_reg_cancel_clicked,
                               style='Secondary.TButton')
        cancel_btn.pack(fill='x')

    # Screen transition methods (immediate, no animations)
    def _show_screen_immediate(self, screen_name):
        """Show screen immediately without animation"""
        target_frame = getattr(self, f'{screen_name}_frame', None)
        
        if not target_frame or not self._is_valid_widget(target_frame):
            logger.warning(f"Cannot show screen '{screen_name}' - frame not found or invalid")
            return

        # Hide current screen first
        if self._current_screen:
            current_frame = getattr(self, f'{self._current_screen}_frame', None)
            if current_frame and self._is_valid_widget(current_frame):
                try:
                    current_frame.place_forget()
                except tk.TclError:
                    pass

        # Show target screen immediately
        try:
            target_frame.place(x=0, y=0, relwidth=1.0, relheight=1.0)
            self._current_screen = screen_name
            logger.debug(f"Switched to {screen_name} screen immediately")
        except tk.TclError as e:
            logger.error(f"Error showing screen {screen_name}: {e}")

    def show_main_screen(self):
        """Switch to the main menu screen immediately"""
        self.stop_video()
        
        # Stop any active monitoring when returning to main menu
        try:
            # Stop hand washing monitoring if active
            if hasattr(self, 'handwash_callbacks') and self.handwash_callbacks.get('stop_cb'):
                logger.info("Stopping hand washing monitoring when returning to main menu")
                self.handwash_callbacks['stop_cb']()
            
            # Stop face registration if active
            if hasattr(self, 'face_reg_callbacks') and self.face_reg_callbacks.get('stop_cb'):
                logger.info("Stopping face registration when returning to main menu")
                self.face_reg_callbacks['stop_cb']()
                
        except Exception as e:
            logger.error(f"Error stopping monitoring when returning to main: {e}", exc_info=True)
        
        self._show_screen_immediate('main')

    def show_processing_screen(self):
        """Switch to the processing screen immediately"""
        self._show_screen_immediate('processing')

    def show_record_screen(self):
        """Switch to the record viewing screen immediately"""
        self.stop_video()
        self._show_screen_immediate('record')
        # Show the default table after brief delay
        self.root.after(100, lambda: self.show_records_table(1))

    def show_handwash_screen(self):
        """Switch to the hand washing monitoring screen immediately"""
        self._show_screen_immediate('handwash')

    def show_face_registration_screen(self):
        """Switch to the face registration screen immediately"""
        self._show_screen_immediate('face_registration')

    # Enhanced status setting (immediate, no animations)
    def set_status(self, status_text, status_type="info"):
        """Set the status message with immediate styling (no animations)"""
        def _update_status():
            if self._destroyed or not self._is_valid_widget(self.status_label):
                return
            
            # Color mapping for different status types
            color_map = {
                "success": self.colors['success'],
                "error": self.colors['error'], 
                "warning": self.colors['warning'],
                "info": self.colors['info'],
                "default": self.colors['hover_medium']
            }
            
            target_color = color_map.get(status_type, color_map["default"])
            
            # Set text and color immediately
            self.status_var.set(status_text)
            try:
                self.status_label.configure(bg=target_color)
            except tk.TclError:
                pass

        if self._is_valid_widget(self.root):
            self.root.after(0, _update_status)

    # Keep all existing methods with minimal changes
    def refresh_current_table(self):
        """Refresh the currently displayed table"""
        try:
            logger.debug(f"Refreshing records table {self.current_table_id}")
            self.record_search_var.set("")
            self.show_records_table(self.current_table_id)
        except Exception as e:
            logger.error(f"Error refreshing table: {e}", exc_info=True)

    def on_record_search(self, *args):
        """Filter the records table based on search text"""
        try:
            search_text = self.record_search_var.get().lower().strip()
            logger.debug(f"Filtering table {self.current_table_id} with search: '{search_text}'")

            treeview = None
            if self._is_valid_widget(self.table_frame):
                for child in self.table_frame.winfo_children():
                    if isinstance(child, ttk.Treeview):
                        treeview = child
                        break
            
            if not treeview or not self._is_valid_widget(treeview):
                logger.warning("No valid treeview found in table_frame to filter.")
                return

            all_items = list(treeview.get_children(''))
            item_data = {item: treeview.item(item, 'values') for item in all_items}
            treeview.detach(*all_items)

            match_count = 0
            for i, (item, values) in enumerate(item_data.items()):
                match = False
                if not search_text:
                    match = True
                else:
                    if any(search_text in str(value).lower() for value in values):
                        match = True

                if match:
                    tag = 'evenrow' if match_count % 2 == 0 else 'oddrow'
                    treeview.reattach(item, '', 'end')
                    treeview.item(item, tags=(tag,))
                    match_count += 1
            
            logger.debug(f"Found {match_count} matching records for search.")
            
        except Exception as e:
            logger.error(f"Error in record search: {e}", exc_info=True)

    # Button handlers (keeping existing functionality)
    def on_start_clicked(self):
        """Handle start button click"""
        try:
            logger.info("Start button clicked")
            if self.start_callback:
                self.start_callback()
        except Exception as e:
            logger.error(f"Error in start button callback: {e}", exc_info=True)

    def on_record_clicked(self):
        """Handle record button click"""
        try:
            logger.info("Record button clicked") 
            if self.record_callback:
                self.record_callback()
        except Exception as e:
            logger.error(f"Error in record button callback: {e}", exc_info=True)

    def on_manage_clicked(self):
        """Handle manage workers button click"""
        try:
            logger.info("Manage workers button clicked")
            if self.manage_callback:
                self.manage_callback()
            else:
                logger.warning("Manage workers clicked but manage_callback is not set.")
                if self._is_valid_widget(self.root):
                    messagebox.showwarning("Not Available", "Worker management not configured.", parent=self.root)
        except Exception as e:
            logger.error(f"Error in manage button callback: {e}", exc_info=True)

    def on_handwash_clicked(self):
        """Handle hand washing button click"""
        try:
            logger.info("Hand washing button clicked")
            if self.handwash_callback:
                self.handwash_callback()
            else:
                logger.warning("Hand washing clicked but handwash_callback is not set.")
                if self._is_valid_widget(self.root):
                    messagebox.showwarning("Not Available", "Hand washing monitor not configured.", parent=self.root)
        except Exception as e:
            logger.error(f"Error in hand washing button callback: {e}", exc_info=True)

    def on_main_face_reg_clicked(self):
        """Handle face registration button click from main menu"""
        try:
            logger.info("Main face registration button clicked")
            self.face_reg_source = 'main'  # Mark that we came from main menu
            if hasattr(self, 'face_reg_callbacks') and self.face_reg_callbacks.get('register_face_cb'):
                self.face_reg_callbacks['register_face_cb']()
            else:
                logger.warning("Face registration clicked but callback not set.")
                if self._is_valid_widget(self.root):
                    messagebox.showwarning("Not Available", "Face registration not configured.", parent=self.root)
        except Exception as e:
            logger.error(f"Error in main face registration button callback: {e}", exc_info=True)

    def on_cancel_clicked(self):
        """Handle click on Cancel button during processing"""
        try:
            logger.info("Cancel button clicked during processing")
            if self.start_callback:
                 logger.debug("Signaling app to stop and restart via start_callback.")
                 self.start_callback()
            else:
                 logger.warning("Cancel clicked but start_callback is not set.")
                 self.show_main_screen()
        except Exception as e:
            logger.error(f"Error in cancel button callback: {e}", exc_info=True)

    def on_exit_clicked(self):
        """Handle exit button click"""
        try:
            logger.info("Exit button clicked")
            if self.exit_callback:
                if self._is_valid_widget(self.root) and messagebox.askyesno("Confirm Exit", "Are you sure you want to exit the application?", parent=self.root):
                    self.exit_callback()
            else:
                if messagebox.askyesno("Confirm Exit", "Exit application?", parent=self.root):
                     if self._is_valid_widget(self.root):
                         self.root.destroy()
        except Exception as e:
            logger.error(f"Error in exit button callback: {e}", exc_info=True)

    def set_callbacks(self, start_cb=None, record_cb=None, exit_cb=None, manage_cb=None, handwash_cb=None):
        """Set callback functions for buttons"""
        self.start_callback = start_cb
        self.record_callback = record_cb
        self.exit_callback = exit_cb
        self.manage_callback = manage_cb
        self.handwash_callback = handwash_cb

    def set_database(self, db):
        """Set the database reference"""
        self.db = db
        logger.info("Database reference set in GUI")

    def show_records_table(self, table_id):
        """Display selected records table (requests data from app)"""
        try:
            logger.info(f"Requesting records display for table {table_id}")
            self.current_table_id = table_id

            if self._is_valid_widget(self.table_frame):
                for widget in self.table_frame.winfo_children():
                    if self._is_valid_widget(widget):
                        widget.destroy()

                loading_label = tk.Label(self.table_frame, 
                                       text="Loading records...", 
                                       font=('SF Pro Display', 14, 'normal') if self._font_exists('SF Pro Display') else ('Segoe UI', 14, 'normal'),
                                       fg=self.colors['text_secondary'],
                                       bg=self.colors['card_bg'])
                loading_label.pack(pady=40)

            if hasattr(self, 'record_callback') and self.record_callback:
                app_instance = getattr(self.record_callback, '__self__', None)
                if app_instance and hasattr(app_instance, 'update_records_display'):
                     if self._is_valid_widget(self.root):
                         self.root.after(10, app_instance.update_records_display, table_id)
                else:
                     logger.error("Record callback is not configured correctly to update display.")
                     if self._is_valid_widget(loading_label):
                         loading_label.config(text="Error: Cannot load records.")
            else:
                 logger.error("Record callback not set.")
                 if 'loading_label' in locals() and self._is_valid_widget(loading_label):
                     loading_label.config(text="Error: Callback not configured.")
        
        except Exception as e:
            logger.error(f"Error showing records table: {e}", exc_info=True)

    def update_records_table(self, table_id, data):
        """Update the records table with actual data (called by app.py)"""
        try:
            logger.info(f"Updating GUI records table {table_id} with {len(data)} records")
            if self._destroyed or not self._is_valid_widget(self.root):
                return

            if table_id != self.current_table_id:
                logger.warning(f"Received update for table {table_id}, but currently showing {self.current_table_id}. Ignoring.")
                return

            if self._is_valid_widget(self.table_frame):
                for widget in self.table_frame.winfo_children():
                    if self._is_valid_widget(widget):
                        widget.destroy()

            # Table configuration
            columns = []
            heading_text = []
            col_widths = {}

            if table_id == 1:
                columns = ["serial", "worker", "registered", "first_use"]
                heading_text = ["Serial Number", "Worker Name", "Registered On", "First Use On"]
                col_widths = {"serial": 140, "worker": 180, "registered": 140, "first_use": 140}
            elif table_id == 2:
                columns = ["serial", "datetime", "worker"]
                heading_text = ["Serial Number", "Wash Date/Time", "Worker Name"]
                col_widths = {"serial": 150, "datetime": 180, "worker": 180}
            elif table_id == 3:
                columns = ["worker", "datetime", "compliant"]
                heading_text = ["Worker Name", "Date/Time", "Compliant"]
                col_widths = {"worker": 180, "datetime": 180, "compliant": 80}
            elif table_id == 4:
                columns = ["worker", "duration", "quality", "datetime"]
                heading_text = ["Worker Name", "Duration", "Quality", "Date/Time"]
                col_widths = {"worker": 150, "duration": 100, "quality": 100, "datetime": 180}

            # Create modern table container
            table_container = tk.Frame(self.table_frame, bg=self.colors['card_bg'])
            table_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

            # Create the treeview with enhanced styling
            tree = ttk.Treeview(table_container, columns=columns, show='headings')

            # Enhanced row styling
            tree.tag_configure('oddrow', background=self.colors['bg_primary'])
            tree.tag_configure('evenrow', background=self.colors['card_bg'])

            # Set column headings and widths
            for i, col in enumerate(columns):
                width = col_widths.get(col, 120)
                tree.heading(col, text=heading_text[i], anchor=tk.W)
                tree.column(col, width=width, anchor=tk.W, stretch=tk.NO)

            # Premium scrollbars
            y_scrollbar = ttk.Scrollbar(table_container, orient=tk.VERTICAL, command=tree.yview)
            tree.configure(yscroll=y_scrollbar.set)

            x_scrollbar = ttk.Scrollbar(table_container, orient=tk.HORIZONTAL, command=tree.xview)
            tree.configure(xscroll=x_scrollbar.set)

            # Pack with proper layout
            y_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            x_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            # Populate with data
            if data:
                for i, record in enumerate(data):
                    tag = 'evenrow' if i % 2 == 0 else 'oddrow'
                    if len(record) == len(columns):
                         tree.insert('', 'end', values=record, tags=(tag,))
                    else:
                         logger.warning(f"Record data mismatch for table {table_id}: expected {len(columns)} cols, got {len(record)}. Record: {record}")
                         error_val = ("Data Error",) + ("",) * (len(columns) - 1)
                         tree.insert('', 'end', values=error_val, tags=(tag,))

                self.status_var.set(f"Loaded {len(data)} records")
            else:
                no_data_val = ("No records found",) + ("",) * (len(columns) - 1)
                tree.insert('', 'end', values=no_data_val, tags=('oddrow',))
                self.status_var.set("No records found")
                
        except Exception as e:
            logger.error(f"Error updating records table: {e}", exc_info=True)

    def stop_video(self):
        """Stop video display and reset frame rate"""
        self.video_running = False

        # Reset video frame rate to default after processing
        self._video_update_interval = 16  # Reset to default ~60 FPS

        if self.video_thread and self.video_thread.is_alive():
            logger.info("Joining video thread...")
            self.video_thread.join(timeout=2.0)
            if self.video_thread.is_alive():
                logger.warning("Video thread did not terminate properly")
            else:
                logger.info("Video thread terminated cleanly")

        self.video_thread = None

        # Clear video display
        if self._is_valid_widget(self.video_label):
            def _clear_label():
                if self._is_valid_widget(self.video_label):
                    try:
                        self.video_label.configure(image='', text="No Video")
                        # Clear image reference
                        if hasattr(self, 'display_image'):
                            self.display_image = None
                    except tk.TclError as e:
                        logger.debug(f"TclError clearing video label: {e}")

            if self._is_valid_widget(self.root):
                self.root.after(0, _clear_label)

        # Stop and hide progress bar
        if self._is_valid_widget(self.progress_bar):
            try:
                self.progress_bar.stop()
                self.progress_bar.pack_forget()
            except tk.TclError:
                pass

        # Clean up image references
        self._image_references.clear()
        
        logger.info("Video display stopped and frame rate reset")

    def show_video(self, frame):
        """Show a video frame in the processing screen with enhanced error handling"""
        if frame is None or not self.video_running or not self._is_valid_widget(self.video_label):
             return

        try:
            # Validate frame
            if not isinstance(frame, np.ndarray) or len(frame.shape) != 3:
                logger.warning("Invalid frame format received")
                return
            
            if frame.shape[2] != 3:
                logger.warning(f"Invalid frame channels: {frame.shape[2]}, expected 3")
                return
            
            # Frame is already mirrored with annotations in correct positions
            img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img_pil = Image.fromarray(img_rgb)

            # Optimize: Use label dimensions more efficiently
            label_width = self.video_label.winfo_width()
            label_height = self.video_label.winfo_height()

            # Use more reasonable max dimensions for better performance
            max_width = min(label_width if label_width > 10 else self.max_video_width, 600)  # Adjusted for portrait
            max_height = min(label_height if label_height > 10 else self.max_video_height, 800)  # Adjusted for portrait

            # Only resize if needed (avoid unnecessary processing)
            if img_pil.width > max_width or img_pil.height > max_height:
                img_pil.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

            old_image = self.display_image
            new_image = ImageTk.PhotoImage(img_pil)
            self.display_image = new_image
            
            # Update the label with new image
            self.video_label.config(image=self.display_image, text="")
            
            # Optimize: More aggressive memory management
            self._image_references.append(new_image)
            if len(self._image_references) > 3:  # Reduced from 5 for better memory usage
                old_refs = self._image_references[:-3]
                self._image_references = self._image_references[-3:]
                
                for old_ref in old_refs:
                    try:
                        del old_ref
                    except:
                        pass
            
            if old_image and old_image != new_image:
                try:
                    del old_image
                except:
                    pass

        except Exception as e:
            logger.error(f"Error displaying video frame: {e}", exc_info=True)
            # Don't stop video on single frame errors
            pass

    def start_video(self, frame_callback):
        """Start video display thread"""
        if self.video_running:
            logger.warning("Video already running, ignoring start request.")
            return

        self.video_running = True
        logger.info("Starting video display thread")

        if self._is_valid_widget(self.progress_bar):
             if not self.progress_bar.winfo_ismapped():
                 self.progress_bar.pack(fill=tk.X, pady=2)
             self.progress_bar.start(10)

        self.video_thread = threading.Thread(target=self._video_loop, args=(frame_callback,), name="VideoLoopThread")
        self.video_thread.daemon = True
        self.video_thread.start()

    def _video_loop(self, frame_callback):
        """Video display loop running in background thread with improved error handling"""
        logger.info("Video loop started")
        
        # Use configurable update interval for frame rate control
        frame_sleep_time = max(0.008, self._video_update_interval / 1000.0)  # Convert ms to seconds
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        while self.video_running and not self._destroyed:
            try:
                # Get frame from callback
                frame = frame_callback()
                if frame is not None and self.video_running:
                    if self._is_valid_widget(self.root):
                         self.root.after(0, self.show_video, frame)
                         consecutive_errors = 0  # Reset error counter
                    else:
                         logger.warning("Root window destroyed, stopping video loop.")
                         break
                else:
                     # Wait briefly when no frame available
                     time.sleep(0.02)
                     continue

            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error in video loop (attempt {consecutive_errors}): {e}", exc_info=True)
                
                if consecutive_errors >= max_consecutive_errors:
                    logger.error("Too many consecutive errors in video loop, stopping")
                    break
                    
                time.sleep(0.5)

            # Controlled frame rate based on current video update interval
            time.sleep(frame_sleep_time)

        logger.info("Video loop ended")
        if self._is_valid_widget(self.root):
            def _stop_progress():
                if self._is_valid_widget(self.progress_bar):
                    try:
                        self.progress_bar.stop()
                        self.progress_bar.pack_forget()
                    except tk.TclError:
                        pass
            self.root.after(0, _stop_progress)

    def set_worker_info(self, worker_name, serial_number):
        """Update worker and serial number information"""
        def _update_info():
            if self._destroyed:
                return
            self.worker_var.set(worker_name if worker_name else "N/A")
            self.serial_var.set(serial_number if serial_number else "")

        if self._is_valid_widget(self.root):
            self.root.after(0, _update_info)

    def on_closing(self):
        """Handle window closing with comprehensive cleanup"""
        try:
            logger.info("GUI closing requested")
            self._destroyed = True
            
            # Stop video first
            self.stop_video()
            
            # Clean up all image references
            self._cleanup_image_references()
            
            if self.exit_callback:
                if messagebox.askyesno("Confirm Exit", "Are you sure you want to exit the application?", parent=self.root):
                     self.exit_callback()
                else:
                     logger.info("User cancelled exit.")
                     self._destroyed = False
                     return
            else:
                if messagebox.askyesno("Confirm Exit", "Exit application?", parent=self.root):
                     if self._is_valid_widget(self.root):
                         self.root.destroy()
                else:
                     self._destroyed = False
                     
        except Exception as e:
            logger.error(f"Error during GUI closing: {e}", exc_info=True)
            if self._is_valid_widget(self.root):
                self.root.destroy()

    def on_handwash_start_clicked(self):
        """Handle start hand washing monitoring button click"""
        try:
            logger.info("Hand washing start monitoring button clicked")
            if hasattr(self, 'handwash_callbacks') and self.handwash_callbacks.get('start_cb'):
                self.handwash_callbacks['start_cb']()
            else:
                logger.warning("Hand washing start clicked but callback not set.")
                if self._is_valid_widget(self.root):
                    messagebox.showwarning("Not Available", "Hand washing start monitoring not configured.", parent=self.root)
        except Exception as e:
            logger.error(f"Error in hand washing start callback: {e}", exc_info=True)

    def on_handwash_stop_clicked(self):
        """Handle stop hand washing monitoring button click"""
        try:
            logger.info("Hand washing stop monitoring button clicked")
            if hasattr(self, 'handwash_callbacks') and self.handwash_callbacks.get('stop_cb'):
                self.handwash_callbacks['stop_cb']()
            else:
                logger.warning("Hand washing stop clicked but callback not set.")
                if self._is_valid_widget(self.root):
                    messagebox.showwarning("Not Available", "Hand washing stop monitoring not configured.", parent=self.root)
        except Exception as e:
            logger.error(f"Error in hand washing stop callback: {e}", exc_info=True)

    def on_register_face_clicked(self):
        """Handle register face button click - transition to face registration screen"""
        logger.info("Register worker face button clicked - transitioning to face registration screen")
        self.face_reg_source = 'handwash'  # Mark that we came from handwash screen
        self.show_face_registration_screen()

    def on_face_reg_start_clicked(self):
        """Handle face registration start button click"""
        try:
            worker_name = self.face_reg_name_var.get().strip()
            if not worker_name:
                self.face_reg_status_var.set("Please enter a worker name first")
                return
            
            # Start the face registration process
            if hasattr(self, 'face_reg_callbacks') and self.face_reg_callbacks.get('start_cb'):
                self.face_reg_callbacks['start_cb'](worker_name)
            else:
                self.face_reg_status_var.set("Face registration system not available")
        except Exception as e:
            logger.error(f"Error starting face registration: {e}", exc_info=True)
            self.face_reg_status_var.set(f"Error: {str(e)}")

    def on_face_reg_stop_clicked(self):
        """Handle face registration stop button click"""
        try:
            if hasattr(self, 'face_reg_callbacks') and self.face_reg_callbacks.get('stop_cb'):
                self.face_reg_callbacks['stop_cb']()
            else:
                self.face_reg_status_var.set("Face registration system not available")
        except Exception as e:
            logger.error(f"Error stopping face registration: {e}", exc_info=True)
            self.face_reg_status_var.set(f"Error: {str(e)}")

    def on_face_reg_cancel_clicked(self):
        """Handle face registration cancel button click"""
        logger.info("Face registration cancel button clicked - cleaning up and returning to handwash screen")
        try:
            # Call stop callback if available
            if hasattr(self, 'face_reg_callbacks') and self.face_reg_callbacks.get('stop_cb'):
                self.face_reg_callbacks['stop_cb']()
        except Exception as e:
            logger.error(f"Error in face registration stop callback: {e}", exc_info=True)
        
        # Return to handwash screen
        self.show_handwash_screen()

    def on_face_reg_back_clicked(self):
        """Handle face registration back button click - return to source screen"""
        logger.info("Face registration back button clicked")
        try:
            # Call stop callback if available to ensure camera is stopped
            if hasattr(self, 'face_reg_callbacks') and self.face_reg_callbacks.get('stop_cb'):
                logger.info("Stopping face registration and camera via callback")
                self.face_reg_callbacks['stop_cb']()
            else:
                logger.warning("Face registration stop callback not available")
        except Exception as e:
            logger.error(f"Error in face registration stop callback: {e}", exc_info=True)
        
        # Return to source screen
        if self.face_reg_source == 'handwash':
            logger.info("Returning to hand washing screen")
            self.show_handwash_screen()
        else:
            logger.info("Returning to main screen")
            self.show_main_screen()

    def set_handwash_callbacks(self, start_cb=None, stop_cb=None, register_face_cb=None):
        """Set callbacks for hand washing operations"""
        self.handwash_callbacks = {
            'start_cb': start_cb,
            'stop_cb': stop_cb,
            'register_face_cb': register_face_cb
        }

    def set_face_reg_callbacks(self, start_cb=None, stop_cb=None, video_cb=None, register_face_cb=None):
        """Set callbacks for face registration operations"""
        self.face_reg_callbacks = {
            'start_cb': start_cb,
            'stop_cb': stop_cb,
            'video_cb': video_cb,
            'register_face_cb': register_face_cb
        }

    def update_face_reg_status(self, status_text=None, progress_text=None, progress_value=None):
        """Update face registration status and progress"""
        try:
            if status_text and hasattr(self, 'face_reg_status_var'):
                self.face_reg_status_var.set(status_text)
            if progress_text and hasattr(self, 'face_reg_progress_var'):
                self.face_reg_progress_var.set(progress_text)
            if progress_value is not None and hasattr(self, 'face_reg_progress'):
                self.face_reg_progress['value'] = progress_value
        except Exception as e:
            logger.error(f"Error updating face registration status: {e}")

    def show_face_reg_video(self, frame):
        """Display frame in face registration video area"""
        try:
            if frame is not None and hasattr(self, 'face_reg_video_label'):
                from PIL import Image, ImageTk
                import cv2
                
                # Frame is already mirrored with overlay in correct positions
                
                # Optimize: Resize frame for display (smaller for better performance)
                display_frame = cv2.resize(frame, (540, 720))  # Portrait size for better display
                
                # Convert BGR to RGB
                rgb_frame = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
                
                # Convert to PhotoImage
                pil_image = Image.fromarray(rgb_frame)
                photo = ImageTk.PhotoImage(pil_image)
                
                # Update video label
                self.face_reg_video_label.configure(image=photo, text="")
                self.face_reg_video_label.image = photo  # Keep a reference
        except Exception as e:
            logger.error(f"Error displaying face registration video: {e}")

    def update_handwash_status(self, worker_name="None Detected", duration="0.0s", quality="Not Washing", quality_color=None):
        """Update hand washing status display"""
        try:
            if hasattr(self, 'handwash_worker_label'):
                self.handwash_worker_label.config(text=worker_name)
            if hasattr(self, 'handwash_duration_label'):
                self.handwash_duration_label.config(text=duration)
            if hasattr(self, 'handwash_quality_label'):
                self.handwash_quality_label.config(text=quality)
                
            # Update quality label color if provided
            if quality_color and hasattr(self, 'handwash_quality_label'):
                try:
                    self.handwash_quality_label.config(fg=quality_color)
                except tk.TclError:
                    pass
        except Exception as e:
            logger.error(f"Error updating handwash status: {e}", exc_info=True)

    def show_handwash_video(self, frame):
        """Show a video frame in the hand washing screen"""
        if frame is None or not hasattr(self, 'handwash_video_label') or not self._is_valid_widget(self.handwash_video_label):
            return

        try:
            from PIL import Image, ImageTk
            import cv2
            
            # Frame is already mirrored with annotations in correct positions
            img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img_pil = Image.fromarray(img_rgb)

            # Optimize: Use smaller thumbnail size for better performance
            img_pil.thumbnail((540, 720), Image.Resampling.LANCZOS)  # Portrait size for better display

            handwash_image = ImageTk.PhotoImage(img_pil)
            self.handwash_video_label.config(image=handwash_image, text="")
            # Keep a reference to prevent garbage collection
            self.handwash_video_label.image = handwash_image

        except Exception as e:
            logger.error(f"Error displaying handwash video frame: {e}", exc_info=True)

    def toggle_fullscreen(self, event):
        """Toggle fullscreen mode"""
        self.root.attributes('-fullscreen', not self.root.attributes('-fullscreen'))

    def force_quit(self, event):
        """Force quit the application"""
        self.root.quit()

    def show_fullscreen_decision(self, decision_type, message, missing_ppe=None, organized_status=None):
        """Show fullscreen access decision for 3 seconds with appropriate colors and organized PPE status"""
        try:
            # Create fullscreen overlay
            self.decision_overlay = tk.Toplevel(self.root)
            self.decision_overlay.title("Access Decision")
            self.decision_overlay.attributes('-fullscreen', True)
            self.decision_overlay.attributes('-topmost', True)
            self.decision_overlay.grab_set()  # Make it modal
            
            # Set background color based on decision type
            if decision_type == "approved":
                bg_color = self.colors['success']  # Green
                text_color = 'white'
                main_text = "ACCESS APPROVED"
                icon_text = "✓"
            else:  # denied
                bg_color = self.colors['error']  # Red
                text_color = 'white'
                main_text = "ACCESS DENIED"
                icon_text = "✗"
            
            self.decision_overlay.configure(bg=bg_color)
            
            # Main container
            main_frame = tk.Frame(self.decision_overlay, bg=bg_color)
            main_frame.pack(expand=True, fill='both')
            
            # Center content vertically and horizontally
            content_frame = tk.Frame(main_frame, bg=bg_color)
            content_frame.place(relx=0.5, rely=0.5, anchor='center')
            
            # Large icon
            icon_label = tk.Label(content_frame, 
                                text=icon_text,
                                font=('SF Pro Display', 120, 'bold') if self._font_exists('SF Pro Display') else ('Segoe UI', 120, 'bold'),
                                fg=text_color,
                                bg=bg_color)
            icon_label.pack(pady=(0, 30))
            
            # Main decision text
            decision_label = tk.Label(content_frame,
                                    text=main_text,
                                    font=('SF Pro Display', 48, 'bold') if self._font_exists('SF Pro Display') else ('Segoe UI', 48, 'bold'),
                                    fg=text_color,
                                    bg=bg_color)
            decision_label.pack(pady=(0, 20))
            
            # Additional message
            if message:
                message_label = tk.Label(content_frame,
                                       text=message,
                                       font=('SF Pro Display', 24, 'normal') if self._font_exists('SF Pro Display') else ('Segoe UI', 24, 'normal'),
                                       fg=text_color,
                                       bg=bg_color,
                                       wraplength=800,
                                       justify='center')
                message_label.pack(pady=(0, 30))
            
            # PPE status information for denied cases
            if decision_type == "denied":
                ppe_frame = tk.Frame(content_frame, bg=bg_color)
                ppe_frame.pack(pady=(0, 20))
                
                # Show organized status if available, otherwise fall back to missing PPE
                if organized_status and len(organized_status) > 0:
                    ppe_title = tk.Label(ppe_frame,
                                       text="PPE Detection Status:",
                                       font=('SF Pro Display', 20, 'bold') if self._font_exists('SF Pro Display') else ('Segoe UI', 20, 'bold'),
                                       fg=text_color,
                                       bg=bg_color)
                    ppe_title.pack(pady=(0, 10))
                    
                    # Display each status line with appropriate coloring
                    for status_line in organized_status:
                        # Determine text color based on status
                        if "Absent" in status_line or "Incomplete" in status_line:
                            line_color = '#FFB6C1'  # Light pink for issues
                            font_weight = 'bold'
                        else:
                            line_color = '#90EE90'  # Light green for present
                            font_weight = 'normal'
                        
                        status_label = tk.Label(ppe_frame,
                                              text=status_line,
                                              font=('SF Pro Display', 16, font_weight) if self._font_exists('SF Pro Display') else ('Segoe UI', 16, font_weight),
                                              fg=line_color,
                                              bg=bg_color)
                        status_label.pack(pady=2)
                
                elif missing_ppe:
                    # Fallback to traditional missing PPE display
                    ppe_title = tk.Label(ppe_frame,
                                       text="Missing PPE:",
                                       font=('SF Pro Display', 20, 'bold') if self._font_exists('SF Pro Display') else ('Segoe UI', 20, 'bold'),
                                       fg=text_color,
                                       bg=bg_color)
                    ppe_title.pack()
                    
                    # Format missing PPE list
                    if isinstance(missing_ppe, list):
                        ppe_text = ", ".join([ppe.title() for ppe in missing_ppe])
                    else:
                        ppe_text = str(missing_ppe)
                    
                    ppe_list = tk.Label(ppe_frame,
                                      text=ppe_text,
                                      font=('SF Pro Display', 18, 'normal') if self._font_exists('SF Pro Display') else ('Segoe UI', 18, 'normal'),
                                      fg=text_color,
                                      bg=bg_color,
                                      wraplength=600,
                                      justify='center')
                    ppe_list.pack(pady=(10, 0))
            
            # Countdown timer display
            self.countdown_label = tk.Label(content_frame,
                                          text="",
                                          font=('SF Pro Display', 16, 'normal') if self._font_exists('SF Pro Display') else ('Segoe UI', 16, 'normal'),
                                          fg=text_color,
                                          bg=bg_color)
            self.countdown_label.pack(pady=(30, 0))
            
            # Start countdown timer
            self._start_countdown(3)
            
            # Auto-close after 3 seconds
            self.root.after(3000, self._close_fullscreen_decision)
            
            logger.info(f"Showing fullscreen {decision_type} decision for 3 seconds")
            
        except Exception as e:
            logger.error(f"Error showing fullscreen decision: {e}", exc_info=True)

    def _start_countdown(self, seconds):
        """Start countdown timer display"""
        if hasattr(self, 'countdown_label') and self._is_valid_widget(self.countdown_label):
            self.countdown_label.config(text=f"Returning to main menu in {seconds} seconds...")
            if seconds > 0:
                self.root.after(1000, lambda: self._start_countdown(seconds - 1))

    def _close_fullscreen_decision(self):
        """Close the fullscreen decision overlay"""
        try:
            if hasattr(self, 'decision_overlay') and self._is_valid_widget(self.decision_overlay):
                self.decision_overlay.grab_release()
                self.decision_overlay.destroy()
                logger.info("Fullscreen decision overlay closed")
        except Exception as e:
            logger.error(f"Error closing fullscreen decision: {e}", exc_info=True)

# --- END OF FILE gui.py ---