import pandas as pd
import math
import os

base_dir = os.path.dirname(os.path.abspath(__file__))

# conjunto de todos os municipios (origem e destino) (primeira coluna do CSV: apenas nomes)
M_df = pd.read_csv(os.path.join(base_dir, "caso_nacional", "municipios.csv"))


# Costantes
constantes_df = pd.read_csv(
    os.path.join(base_dir, "caso_nacional", "constantes.csv"), usecols=[0, 1]
).dropna()

col_constante = constantes_df.columns[0]
col_valor = constantes_df.columns[1]

constantes = {
    str(linha[col_constante]).strip(): float(linha[col_valor])
    for _, linha in constantes_df.iterrows()
}

municipios_coordenadas = {
    row["municipio"]: (float(row["latitude"]), float(row["longitude"]))
    for _, row in M_df.iterrows()
}


def haversine(lat1, lon1, lat2, lon2):
    R = float(constantes["raio_terra"])  # raio da Terra em km

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)

    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2)**2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    Delta = R * c

    return Delta  # distância em km


pares_path = os.path.join(base_dir, "caso_nacional",
                          "pares_reais_distancia_rod.csv")
pares_df = pd.read_csv(pares_path)


def calcular_distancia_geodesica(row):
    origem = str(row["origem"]).strip()
    destino = str(row["destino"]).strip()

    if origem not in municipios_coordenadas:
        raise ValueError(f"Municipio de origem nao encontrado: {origem}")
    if destino not in municipios_coordenadas:
        raise ValueError(f"Municipio de destino nao encontrado: {destino}")

    lat1, lon1 = municipios_coordenadas[origem]
    lat2, lon2 = municipios_coordenadas[destino]

    return haversine(lat1, lon1, lat2, lon2)


pares_df["distancia_geodesica(km)"] = pares_df.apply(
    calcular_distancia_geodesica, axis=1)

pares_df["Beta"] = pares_df["distancia_real(km)"] / \
    pares_df["distancia_geodesica(km)"]

beta_stats = pares_df["Beta"]
print(f"Beta - media: {beta_stats.mean()}")
print(f"Beta - mediana: {beta_stats.median()}")
print(f"Beta - desvio_padrao: {beta_stats.std()}")
print(f"Beta - minimo: {beta_stats.min()}")
print(f"Beta - maximo: {beta_stats.max()}")

pares_df.to_csv(pares_path, index=False)
