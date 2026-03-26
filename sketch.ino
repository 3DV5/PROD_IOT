#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ===== Configurações Wi-Fi =====
const char* ssid = "Wokwi-GUEST";
const char* password = "";

// ===== Configurações MQTT (TLS) =====
const char* mqtt_server = "d075579b224c4899825f05c9f9a1ec5a.s1.eu.hivemq.cloud";
const int mqtt_port = 8883;
const char* mqtt_user = "teste123";
const char* mqtt_password = "Eduardo123";
const char* client_id = "ESP32_CaixaDagua_01";

// Tópicos
const char* topico_nivel = "uepa/dupla1/water_level";
const char* topico_status = "uepa/dupla1/status";

WiFiClientSecure espClient;
PubSubClient client(espClient);

// ===== Variáveis de tempo e dados =====
unsigned long ultimoEnvioSensor = 0;
unsigned long ultimoEnvioStatus = 0;
const long intervalo_sensor = 5000;
const long intervalo_status = 30000;

float nivel_agua = 250.0;      // Nível atual (L)
float vazao = 0.5;             // Consumo (L/s)

// ===== Controle de estados =====
enum Estado {
  DRAINING,      // consumindo água
  FULL_WAIT,     // tanque cheio, aguardando 20s para começar a drenar
  CRITICAL_WAIT, // atingiu 80L, aguardando 20s para começar a encher
  FILLING        // enchendo
};

Estado estado = DRAINING;
unsigned long tempoEntradaEstado = 0;  // millis() quando entrou no estado atual

const float TAXA_ENCHIMENTO = 1.0;   // L/s
const float CAPACIDADE_MAX = 500.0;
const float NIVEL_CRITICO = 80.0;    // 80 litros (16% da capacidade)
const unsigned long TEMPO_ESPERA = 20000; // 20 segundos em ms

// ===== Protótipos =====
void setup_wifi();
void reconnect();
void publicarSensor();
void publicarStatus();

void setup() {
  Serial.begin(115200);
  setup_wifi();
  espClient.setInsecure();
  client.setServer(mqtt_server, mqtt_port);
  tempoEntradaEstado = millis();
}

void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop();

  unsigned long agora = millis();

  if (agora - ultimoEnvioSensor >= intervalo_sensor) {
    ultimoEnvioSensor = agora;
    publicarSensor();
  }

  if (agora - ultimoEnvioStatus >= intervalo_status) {
    ultimoEnvioStatus = agora;
    publicarStatus();
  }

  // Simulação a cada 1 segundo
  static unsigned long ultimaSimulacao = 0;
  if (agora - ultimaSimulacao >= 1000) {
    ultimaSimulacao = agora;

    // Executa a máquina de estados
    switch (estado) {
      case DRAINING:
        // Consumo normal
        nivel_agua -= vazao;

        // Chuva aleatória (10% de chance)
        if (random(100) < 10) {
          float chuva = random(5, 30);
          nivel_agua += chuva;
        }

        // Garante limites
        if (nivel_agua < 0) nivel_agua = 0;
        if (nivel_agua > CAPACIDADE_MAX) nivel_agua = CAPACIDADE_MAX;

        // Verifica se atingiu o nível crítico
        if (nivel_agua <= NIVEL_CRITICO) {
          estado = CRITICAL_WAIT;
          tempoEntradaEstado = agora;
          Serial.println("Nível crítico atingido (80L). Aguardando 20s para encher.");
        }
        break;

      case FULL_WAIT:
        // Mantém o nível cheio, sem consumo, sem chuva
        nivel_agua = CAPACIDADE_MAX;

        // Aguarda o tempo de espera
        if (agora - tempoEntradaEstado >= TEMPO_ESPERA) {
          estado = DRAINING;
          tempoEntradaEstado = agora;
          Serial.println("Iniciando drenagem após 20s de tanque cheio.");
        }
        break;

      case CRITICAL_WAIT:
        // Mantém o nível crítico, sem consumo, sem chuva
        nivel_agua = NIVEL_CRITICO;

        // Aguarda o tempo de espera
        if (agora - tempoEntradaEstado >= TEMPO_ESPERA) {
          estado = FILLING;
          tempoEntradaEstado = agora;
          Serial.println("Iniciando enchimento após 20s de nível crítico.");
        }
        break;

      case FILLING:
        // Enche o tanque
        nivel_agua += TAXA_ENCHIMENTO;
        if (nivel_agua >= CAPACIDADE_MAX) {
          nivel_agua = CAPACIDADE_MAX;
          estado = FULL_WAIT;
          tempoEntradaEstado = agora;
          Serial.println("Tanque cheio. Aguardando 20s para drenar.");
        }
        break;
    }

    // Simula variação da vazão (apenas para DRAINING, mas mantemos para todos os estados)
    vazao = 0.5 + random(-10, 10) / 100.0;
    if (vazao < 0.1) vazao = 0.1;
  }
}

void setup_wifi() {
  delay(10);
  Serial.println();
  Serial.print("Conectando ao Wi-Fi ");
  Serial.println(ssid);

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nWi-Fi conectado!");
  Serial.print("IP: ");
  Serial.println(WiFi.localIP());
}

void reconnect() {
  while (!client.connected()) {
    Serial.print("Tentando conectar MQTT (TLS, sem validação)...");
    if (client.connect(client_id, mqtt_user, mqtt_password)) {
      Serial.println("conectado!");
      client.publish(topico_status, "online", true);
    } else {
      Serial.print("falhou, estado = ");
      Serial.print(client.state());
      Serial.println(" tentando novamente em 5s");
      delay(5000);
    }
  }
}

void publicarSensor() {
  StaticJsonDocument<200> doc;
  doc["nivel"] = nivel_agua;
  doc["vazao"] = vazao;
  doc["enchendo"] = (estado == FILLING);  // indica se está enchendo
  doc["timestamp"] = millis();

  char buffer[200];
  serializeJson(doc, buffer);

  if (client.publish(topico_nivel, buffer)) {
    Serial.print("Dados publicados: ");
    Serial.println(buffer);
  } else {
    Serial.println("Falha ao publicar dados do sensor");
  }
}

void publicarStatus() {
  if (client.publish(topico_status, "online")) {
    Serial.println("Status publicado: online");
  } else {
    Serial.println("Falha ao publicar status");
  }
}