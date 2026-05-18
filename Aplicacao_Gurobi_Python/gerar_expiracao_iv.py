from pathlib import Path

import numpy as np
import pandas as pd

# 1. Carregar os municípios para garantir que os nomes fiquem idênticos
base_dir = Path(__file__).resolve().parent
data_dir = base_dir / "caso_nacional"
df_mun = pd.read_csv(data_dir / "municipios.csv")
municipios = df_mun["municipio"].unique()

# 2. Definir os novos tipos de vacina da Covid-19
vacinas = ["Pfizer", "AstraZeneca", "CoronaVac", "Janssen"]

# 3. Definir a vida útil típica (phi_v) em horas após a saída do ultrafreezer/estoque central
# Pfizer: Cadeia de frio extrema (validade curta em geladeira comum: ~5 dias = 120h)
# CoronaVac: Conservação estável convencional curta (~20 dias = 480h)
# AstraZeneca: Conservação convencional média (~30 dias = 720h)
# Janssen: Conservação estável convencional longa (~45 dias = 1080h)
phi = {"Pfizer": 120, "CoronaVac": 480, "AstraZeneca": 720, "Janssen": 1080}

# Semente aleatória para reprodutibilidade dos dados do seu TCC
np.random.seed(42)

rows_expiracao = []

for mun in municipios:
    for vac in vacinas:
        # Fator de variabilidade randômica na faixa (0.2, 1.0]
        # Representa a fração da validade ainda disponível no momento da redistribuição
        fator_variabilidade = np.random.uniform(0.2, 1.0)

        # Tempo de expiração final e_iv = phi_v * fator
        tempo_expiracao = int(phi[vac] * fator_variabilidade)

        rows_expiracao.append(
            {"municipio": mun, "vacina": vac, "tempo": tempo_expiracao}
        )

# 4. Criar o DataFrame e salvar no caminho correto do seu projeto
df_expiracao = pd.DataFrame(rows_expiracao)
df_expiracao.to_csv(data_dir / "expiracao_iv.csv", index=False)

print("Novo arquivo expiracao_iv.csv gerado com sucesso para o cenário pandêmico!")
print(df_expiracao.head(8))
