import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import paho.mqtt.client as mqtt
import json
import threading
from collections import deque
import sqlite3
import os
from plyer import notification

# ========== CONFIGURAÇÕES MQTT ==========
MQTT_HOST = "d075579b224c4899825f05c9f9a1ec5a.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "teste123"
MQTT_PASSWORD = "Eduardo123"
MQTT_TOPIC_ROOT = "uepa/dupla1"

TOPIC_LEVEL = f"{MQTT_TOPIC_ROOT}/water_level"
TOPIC_STATUS = f"{MQTT_TOPIC_ROOT}/status"

# ========== CONFIGURAÇÕES DO DASHBOARD ==========
TANK_CAPACITY = 500          # litros
MAX_HISTORY = 60             # segundos de histórico para nível e estatísticas
COST_PER_LITER = 0.01        # R$ 0,01 por litro
EMPTY_THRESHOLD = 80         # nível crítico para notificação (agora 80L)

# ========== BANCO DE DADOS ==========
DB_FILE = "water_data.db"

class WaterDashboard:
    def __init__(self):
        # Janela principal
        self.root = ctk.CTk()
        self.root.title("Sistema Inteligente de Gestão de Água - Dashboard")
        self.root.geometry("1400x900")
        self.root.resizable(True, True)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Dados em tempo real (últimos 60 segundos)
        self.water_level = 0.0
        self.flow_rate = 0.0
        self.enchendo = False
        self.timestamps = deque(maxlen=MAX_HISTORY)
        self.levels = deque(maxlen=MAX_HISTORY)
        self.flow_rates = deque(maxlen=MAX_HISTORY)

        # Custo total acumulado (para exibição no painel)
        self.total_cost = 0.0

        # Estado anterior para notificações
        self.prev_enchendo = False
        self.prev_full = False
        self.prev_empty = False

        # Configuração do banco de dados
        self.init_db()

        # Interface
        self.setup_gui()

        # Conecta ao MQTT
        self.setup_mqtt()

        # Atualização periódica dos gráficos
        self.update_graphs_periodically()

        # Inicia o loop da interface
        self.root.mainloop()

    def init_db(self):
        """Cria a tabela SQLite se não existir."""
        self.conn = sqlite3.connect(DB_FILE)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS water_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                level REAL,
                flow_rate REAL,
                cumulative_cost REAL,
                filling BOOLEAN
            )
        ''')
        self.conn.commit()

    def setup_gui(self):
        # Frame principal
        self.main_frame = ctk.CTkFrame(self.root)
        self.main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Esquerda: status e controles
        self.left_frame = ctk.CTkFrame(self.main_frame, width=300)
        self.left_frame.pack(side="left", fill="y", padx=(0, 10), pady=0)

        # Direita: gráficos (3 subplots)
        self.right_frame = ctk.CTkFrame(self.main_frame)
        self.right_frame.pack(side="right", fill="both", expand=True)

        # Título do painel esquerdo
        title = ctk.CTkLabel(self.left_frame, text="Status do Reservatório",
                             font=ctk.CTkFont(size=20, weight="bold"))
        title.pack(pady=(20, 10))

        # Nível
        self.level_label = ctk.CTkLabel(self.left_frame, text="Nível: -- L",
                                        font=ctk.CTkFont(size=18))
        self.level_label.pack(pady=5)

        # Barra de progresso
        self.progress_bar = ctk.CTkProgressBar(self.left_frame, width=200)
        self.progress_bar.pack(pady=10)
        self.progress_bar.set(0)

        # Capacidade
        cap_label = ctk.CTkLabel(self.left_frame, text=f"Capacidade: {TANK_CAPACITY} L",
                                 font=ctk.CTkFont(size=12))
        cap_label.pack(pady=2)

        # Vazão
        self.flow_label = ctk.CTkLabel(self.left_frame, text="Vazão: -- L/s",
                                       font=ctk.CTkFont(size=14))
        self.flow_label.pack(pady=5)

        # Status do nível
        self.status_label = ctk.CTkLabel(self.left_frame, text="Status: Aguardando dados",
                                         font=ctk.CTkFont(size=14, weight="bold"),
                                         text_color="gray")
        self.status_label.pack(pady=10)

        # Custo total acumulado
        self.cost_label = ctk.CTkLabel(self.left_frame, text="Custo Total: R$ 0.00",
                                       font=ctk.CTkFont(size=14))
        self.cost_label.pack(pady=5)

        # Separador
        sep = ctk.CTkFrame(self.left_frame, height=2, fg_color="gray")
        sep.pack(fill="x", pady=10)

        # Informação de conexão MQTT
        self.mqtt_status = ctk.CTkLabel(self.left_frame, text="MQTT: desconectado",
                                        font=ctk.CTkFont(size=12), text_color="red")
        self.mqtt_status.pack(pady=(10, 0))

        # ========== Gráficos ==========
        # Criar figura com 3 subplots verticais
        self.fig, (self.ax_level, self.ax_cost, self.ax_stats) = plt.subplots(3, 1, figsize=(8, 10), dpi=100)
        self.fig.tight_layout(pad=3.0)

        # Configurar gráfico de nível (últimos 60s)
        self.ax_level.set_title("Nível da Água (últimos 60s)")
        self.ax_level.set_ylabel("Litros")
        self.ax_level.set_ylim(0, TANK_CAPACITY)
        self.ax_level.grid(True, linestyle='--', alpha=0.7)
        self.line_level, = self.ax_level.plot([], [], 'b-', linewidth=2)

        # Configurar gráfico de custo histórico mensal
        self.ax_cost.set_title("Custo Acumulado por Mês")
        self.ax_cost.set_ylabel("Reais (R$)")
        self.ax_cost.grid(True, linestyle='--', alpha=0.7)
        # Linhas serão adicionadas dinamicamente

        # Configurar gráfico de estatísticas de consumo (últimos 60s)
        self.ax_stats.set_title("Estatísticas de Consumo (últimos 60s)")
        self.ax_stats.set_ylabel("Vazão (L/s)")
        self.ax_stats.grid(True, linestyle='--', alpha=0.7)
        self.bar_stats = None

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.right_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # Atualiza os gráficos inicialmente
        self.update_graphs()

    def setup_mqtt(self):
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        self.mqtt_client.tls_set()
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        self.mqtt_client.on_disconnect = self.on_disconnect

        def connect_loop():
            try:
                self.mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
                self.mqtt_client.loop_forever()
            except Exception as e:
                print("Erro de conexão MQTT:", e)
                self.mqtt_status.configure(text=f"MQTT: erro ({e})", text_color="red")

        thread = threading.Thread(target=connect_loop, daemon=True)
        thread.start()

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("Conectado ao broker MQTT")
            self.mqtt_status.configure(text="MQTT: conectado", text_color="green")
            client.subscribe(TOPIC_LEVEL)
            client.subscribe(TOPIC_STATUS)
        else:
            print(f"Falha na conexão MQTT, código: {rc}")
            self.mqtt_status.configure(text=f"MQTT: erro {rc}", text_color="red")

    def on_disconnect(self, client, userdata, rc):
        print("Desconectado do MQTT")
        self.mqtt_status.configure(text="MQTT: desconectado", text_color="red")

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode()
        try:
            if topic == TOPIC_LEVEL:
                data = json.loads(payload)
                nivel = data.get("nivel", 0.0)
                vazao = data.get("vazao", 0.0)
                enchendo = data.get("enchendo", False)
                self.root.after(0, self.update_level, nivel, vazao, enchendo)
            elif topic == TOPIC_STATUS:
                status = payload.strip()
                self.root.after(0, self.update_status_label, status)
        except Exception as e:
            print("Erro ao processar mensagem:", e)

    def update_level(self, nivel, vazao, enchendo):
        self.water_level = nivel
        self.flow_rate = vazao
        self.enchendo = enchendo

        # Atualiza labels e barra
        self.level_label.configure(text=f"Nível: {nivel:.1f} L")
        self.progress_bar.set(nivel / TANK_CAPACITY)
        self.flow_label.configure(text=f"Vazão: {vazao:.2f} L/s")

        # Atualiza histórico de 60s
        now = datetime.now()
        self.timestamps.append(now)
        self.levels.append(nivel)
        self.flow_rates.append(vazao)

        # Cálculo do custo incremental (usando a última vazão e tempo decorrido)
        if len(self.timestamps) >= 2:
            delta_t = (now - self.timestamps[-2]).total_seconds()
            if delta_t > 0:
                consumo_litros = vazao * delta_t
                custo = consumo_litros * COST_PER_LITER
                self.total_cost += custo
        else:
            self.total_cost = 0.0

        # Atualiza label de custo total
        self.cost_label.configure(text=f"Custo Total: R$ {self.total_cost:.2f}")

        # Salva no banco de dados
        self.cursor.execute(
            "INSERT INTO water_data (timestamp, level, flow_rate, cumulative_cost, filling) VALUES (?, ?, ?, ?, ?)",
            (now.isoformat(), nivel, vazao, self.total_cost, enchendo)
        )
        self.conn.commit()

        # Verifica e envia notificações
        self.check_notifications(nivel, enchendo)

        # Atualiza status do nível (apenas texto)
        self.check_status()

        # Atualiza os gráficos (via after para segurança)
        self.root.after(0, self.update_graphs)

    def check_notifications(self, nivel, enchendo):
        """Envia notificações OS quando o estado muda."""
        # Notificação de enchimento
        if enchendo and not self.prev_enchendo:
            self.send_notification("Sistema de Água", "Enchimento iniciado! A bomba está enchendo o tanque.")
        # Notificação de tanque cheio
        if nivel >= TANK_CAPACITY and not self.prev_full:
            self.send_notification("Sistema de Água", "Tanque CHEIO! Enchimento parado.")
        # Notificação de nível crítico (vazio) – agora com limite 80L
        if nivel <= EMPTY_THRESHOLD and not self.prev_empty:
            self.send_notification("Sistema de Água", "Nível CRÍTICO! Tanque quase vazio (80L)!")

        # Atualiza estados anteriores
        self.prev_enchendo = enchendo
        self.prev_full = (nivel >= TANK_CAPACITY)
        self.prev_empty = (nivel <= EMPTY_THRESHOLD)

    def send_notification(self, title, message):
        """Envia uma notificação nativa do sistema operacional."""
        try:
            notification.notify(
                title=title,
                message=message,
                timeout=5
            )
        except Exception as e:
            print(f"Erro ao enviar notificação: {e}")

    def update_status_label(self, status):
        # Ignoramos o status "online" do ESP32
        pass

    def check_status(self):
        nivel = self.water_level
        if nivel <= EMPTY_THRESHOLD:
            status = "CRÍTICO - Nível muito baixo!"
            color = "red"
        elif nivel <= 150:
            status = "Baixo (30%) - Enchendo" if self.enchendo else "Baixo (30%)"
            color = "orange"
        elif nivel >= 450:
            status = "Alto (próximo do limite)"
            color = "orange"
        elif nivel >= TANK_CAPACITY:
            status = "CHEIO"
            color = "red"
        else:
            status = "Normal"
            color = "green"
        self.status_label.configure(text=f"Status: {status}", text_color=color)

    def update_graphs_periodically(self):
        # Atualiza gráficos a cada 2 segundos (evita sobrecarga)
        self.update_graphs()
        self.root.after(2000, self.update_graphs_periodically)

    def update_graphs(self):
        # ===== 1. Gráfico de nível (últimos 60s) =====
        if self.timestamps:
            time_strings = [t.strftime("%H:%M:%S") for t in self.timestamps]
            step = max(1, len(time_strings) // 10)
            self.ax_level.clear()
            self.ax_level.plot(range(len(self.levels)), list(self.levels), 'b-', linewidth=2)
            self.ax_level.set_title("Nível da Água (últimos 60s)")
            self.ax_level.set_ylabel("Litros")
            self.ax_level.set_ylim(0, TANK_CAPACITY)
            self.ax_level.grid(True, linestyle='--', alpha=0.7)
            self.ax_level.set_xticks(range(0, len(time_strings), step))
            self.ax_level.set_xticklabels([time_strings[i] for i in range(0, len(time_strings), step)],
                                           rotation=45, ha='right')

        # ===== 2. Gráfico de custo histórico mensal =====
        self.ax_cost.clear()
        # Consulta todos os dados ordenados por timestamp
        self.cursor.execute("SELECT timestamp, cumulative_cost FROM water_data ORDER BY timestamp")
        rows = self.cursor.fetchall()
        if rows:
            # Agrupa por mês
            month_data = {}  # chave: (ano, mês) -> list of (datetime, cumulative_cost)
            for ts_str, cost in rows:
                dt = datetime.fromisoformat(ts_str)
                key = (dt.year, dt.month)
                if key not in month_data:
                    month_data[key] = []
                month_data[key].append((dt, cost))

            # Obtém mês atual e dois anteriores
            today = datetime.now()
            current_month = (today.year, today.month)
            prev_month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
            prev2_month = (today.year, today.month - 2) if today.month > 2 else (today.year - 1, 12 + (today.month - 2))

            # Cores para cada mês
            colors = {'current': 'blue', 'prev': 'green', 'prev2': 'red'}
            labels = {'current': f"{current_month[1]:02d}/{current_month[0]}",
                      'prev': f"{prev_month[1]:02d}/{prev_month[0]}",
                      'prev2': f"{prev2_month[1]:02d}/{prev2_month[0]}"}

            for key, color_name in [('current', current_month), ('prev', prev_month), ('prev2', prev2_month)]:
                if color_name in month_data:
                    points = month_data[color_name]
                    # Ordena por timestamp
                    points.sort(key=lambda x: x[0])
                    dates = [p[0] for p in points]
                    costs = [p[1] for p in points]
                    self.ax_cost.plot(dates, costs, label=labels[key], color=colors[key], linewidth=2)

            self.ax_cost.set_title("Custo Acumulado por Mês")
            self.ax_cost.set_ylabel("Reais (R$)")
            self.ax_cost.grid(True, linestyle='--', alpha=0.7)
            self.ax_cost.legend()
            self.fig.autofmt_xdate()
        else:
            self.ax_cost.set_title("Custo Acumulado por Mês (aguardando dados...)")

        # ===== 3. Gráfico de estatísticas de consumo (últimos 60s) =====
        if self.flow_rates:
            avg = sum(self.flow_rates) / len(self.flow_rates)
            min_val = min(self.flow_rates)
            max_val = max(self.flow_rates)
            self.ax_stats.clear()
            bars = self.ax_stats.bar(['Média', 'Mínimo', 'Máximo'], [avg, min_val, max_val],
                                     color=['blue', 'green', 'red'])
            self.ax_stats.set_title("Estatísticas de Consumo (últimos 60s)")
            self.ax_stats.set_ylabel("Vazão (L/s)")
            self.ax_stats.grid(True, linestyle='--', alpha=0.7)
            for bar in bars:
                height = bar.get_height()
                self.ax_stats.text(bar.get_x() + bar.get_width()/2., height,
                                   f'{height:.2f}', ha='center', va='bottom')
        else:
            self.ax_stats.clear()
            self.ax_stats.set_title("Estatísticas de Consumo (últimos 60s)")
            self.ax_stats.set_ylabel("Vazão (L/s)")
            self.ax_stats.text(0.5, 0.5, "Sem dados", ha='center', va='center', transform=self.ax_stats.transAxes)

        self.canvas.draw_idle()

if __name__ == "__main__":
    app = WaterDashboard()