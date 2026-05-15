import gurobipy as gp
from gurobipy import Model, GRB, quicksum
import pandas as pd
import math
import os
import random

base_dir = os.path.dirname(os.path.abspath(__file__))


def path(*parts):
    return os.path.join(base_dir, *parts)


execucao_df = pd.read_csv(path("caso_nacional", "execucao.csv")).dropna()

# print("Escolha o modelo a ser executado (1 - Intermunicipal):")
# modelo = int(input())
# modelo = 5
# penalidade = False

# Lê e transforma 'Variavel' em índice, depois transpõe (vira de lado)
df = pd.read_csv(path("caso_nacional", "execucao.csv")).set_index('Variavel').T

# Agora 'modelo' e 'penalidade' são colunas acessíveis diretamente
modelo = int(df['modelo'].iloc[0])
penalidade = str(df['penalidade'].iloc[0]).lower() == 'true'

# =========================
# CONJUNTOS
# =========================

# conjunto de todos os municipios (origem e destino) (primeira coluna do CSV: apenas nomes)
M_df = pd.read_csv(path("caso_nacional", "municipios.csv"))

W_rod_mun = {
    str(row["municipio"]): int(row["W_rod"])
    for _, row in M_df.iterrows()
}
W_aereo_mun = {
    str(row["municipio"]): int(row["W_aereo"])
    for _, row in M_df.iterrows()
}

# Criar dicionário: {'Nome_Municipio': 'UF'}

# Mapeamento Municipio -> Estado
estado_de = pd.Series(M_df.estado.values, index=M_df.municipio).to_dict()

# Adicione manualmente as siglas dos estados ao dicionário
# Isso garante que estado_de['RJ'] retorne 'RJ'
for sigla in M_df['estado'].unique():
    estado_de[sigla] = sigla

# Municipios de origem (primeira coluna do CSV: apenas nomes)
I = M_df.iloc[:, 0].dropna().astype(str).tolist()

# Municipios de destino (primeira coluna do CSV: apenas nomes)
J = M_df.iloc[:, 0].dropna().astype(str).tolist()

# Estados (simplificação de "secretaria estadual de saúde")
estados_df = pd.read_csv(path("caso_nacional", "estados.csv"))
K = estados_df.iloc[:, 0].dropna().astype(str).tolist()

W_rod_estado = {
    str(row["estado"]): int(row["W_rod"])
    for _, row in estados_df.iterrows()
}
W_aereo_estado = {
    str(row["estado"]): int(row["W_aereo"])
    for _, row in estados_df.iterrows()
}

# tipos de vacina
V = pd.read_csv(path("caso_nacional", "vacinas.csv"),
                usecols=[0]).iloc[:, 0].dropna().astype(str).tolist()

# Postos por municipio (CSV com duas colunas: municipio, posto)
postos_df = pd.read_csv(path("caso_nacional", "postos.csv"))
col_municipio = "municipio"
col_posto = "posto"

P = {
    municipio: grupo[col_posto].astype(str).tolist()
    for municipio, grupo in postos_df.groupby(col_municipio)
}

# ========================================================
# CONSTANTES
# ========================================================

# Costantes
# Hipotese pedida: capacidade constante por modal, independente da origem/destino.
constantes_df = pd.read_csv(
    path("caso_nacional", "constantes.csv"), usecols=[0, 1]).dropna()
col_constante = constantes_df.columns[0]
col_valor = constantes_df.columns[1]

constantes = {
    str(linha[col_constante]).strip(): float(linha[col_valor])
    for _, linha in constantes_df.iterrows()
}

# ========================================================
# GERAÇÃO DA DEMANDA BASEADA EM POPULAÇÃO
# ========================================================

# Coeficiente para imunidade de rebanho (80%)
epsilon = float(constantes['epsilon'])
# Fator de abrangência/amostragem dos postos selecionados
gamma = float(constantes['gamma'])

rows_demanda_postos = []

for j in J:

    # pega a populacao do municipio j
    pop_j = M_df.loc[M_df['municipio'] == j, 'populacao'].values[0]

    # demanda total do municipio j
    D_j = epsilon * pop_j

    # demandos postos p do municipio j cuio dados dos seus postos foram coletados (P[j])
    d_j = gamma * D_j

    # demanda por posto: d_ip = d_j / | P[j] |
    postos_municipio = P.get(j, [])
    num_postos = len(postos_municipio)

    if num_postos > 0:
        d_ip = int(max(1, d_j / num_postos))

        for p in postos_municipio:
            for v in V:
                # Cada vacina v precisa cobrir a demanda total do posto dip
                rows_demanda_postos.append({
                    "posto": p,
                    "vacina": v,
                    "demanda": d_ip
                })

df_demanda_postos = pd.DataFrame(rows_demanda_postos)
df_demanda_postos.to_csv(
    path("caso_nacional", "demanda_postos.csv"), index=False)


# ========================================================
# PARÂMETROS DE DEMANDA, OFERTA, CAPACIDADE E EXPIRAÇÃO
# ========================================================

# Demanda(necessidade) nos postos
r = {
    (str(linha['posto']), str(linha['vacina'])): int(linha['demanda'])
    for _, linha in df_demanda_postos.iterrows()
}

# Demanda(necessidade) nos postos
# demanda_postos_df = pd.read_csv(
#     "caso_nacional/demanda_postos.csv", usecols=[0, 1, 2]).dropna()
# col_posto = demanda_postos_df.columns[0]
# col_vacina = demanda_postos_df.columns[1]
# col_demanda = demanda_postos_df.columns[2]

# r = {
#     (str(linha[col_posto]), str(linha[col_vacina])): int(linha[col_demanda])
#     for _, linha in demanda_postos_df.iterrows()
# }

# Demanda(necessidade) nos municipios
# A demanda municipal e calculada pela soma das demandas dos postos do municipio.
d = {
    (i, v): sum(r.get((p, v), 0) for p in P.get(i, []))
    for i in I for v in V
}

# Exporta a demanda municipal calculada para CSV.
demanda_municipios_df = pd.DataFrame(
    [
        {"municipio": municipio, "vacina": vacina, "demanda": demanda}
        for (municipio, vacina), demanda in d.items()
    ]
)
demanda_municipios_df.to_csv(
    path("caso_nacional", "demanda_municipios.csv"), index=False)

# ========================================================
# GERAÇÃO DE CENÁRIOS DE OFERTA (INTRA VS INTER)
# ========================================================


rows_intra = []
rows_inter = []

for v in V:
    # -------------------------
    # Demanda total da rede
    # -------------------------
    demanda_total_rede = demanda_municipios_df[
        demanda_municipios_df['vacina'] == v
    ]['demanda'].sum()

    oferta_inter_temp = {}

    # -------------------------
    # Geração base (INTER)
    # -------------------------
    for mun in I:
        demanda_local = demanda_municipios_df[
            (demanda_municipios_df['municipio'] == mun) &
            (demanda_municipios_df['vacina'] == v)
        ]['demanda'].sum()

        # INTRA (autossuficiente)
        eta_intra = random.uniform(1.1, 1.4)
        oferta_intra = int(demanda_local * eta_intra)

        rows_intra.append({
            "municipio": mun,
            "vacina": v,
            "oferta": oferta_intra
        })

        # INTER (com desequilíbrio)
        eta_inter = random.uniform(0.5, 1.5)
        oferta_inter_temp[mun] = demanda_local * eta_inter

    # -------------------------
    # Ajuste global (INTER)
    # -------------------------
    total_oferta_inter = sum(oferta_inter_temp.values())

    if total_oferta_inter < demanda_total_rede:
        kappa = (demanda_total_rede / total_oferta_inter) * 1.1
    else:
        kappa = 1.0

    for mun in I:
        oferta_final = int(oferta_inter_temp[mun] * kappa)

        rows_inter.append({
            "municipio": mun,
            "vacina": v,
            "oferta": oferta_final
        })

# Salva os dois arquivos
pd.DataFrame(rows_intra).to_csv(
    path("caso_nacional", "oferta_o_intra.csv"), index=False)
pd.DataFrame(rows_inter).to_csv(
    path("caso_nacional", "oferta_o_inter.csv"), index=False)


# Oferta nos centros municipais (CSV com colunas: municipio, vacina, oferta)
oferta_o_df = pd.read_csv(path("caso_nacional", "oferta_o.csv"),
                          usecols=[0, 1, 2]).dropna()
col_municipio = oferta_o_df.columns[0]
col_vacina = oferta_o_df.columns[1]
col_oferta = oferta_o_df.columns[2]

o = {
    (str(linha[col_municipio]), str(linha[col_vacina])): int(linha[col_oferta])
    for _, linha in oferta_o_df.iterrows()
}

# Oferta nos centros municipais para o estagio intramunicipal.
# Inicialmente, s e derivado de o (mesmo estoque local de partida).
s = dict(o)


def exportar_oferta_s_csv(s, caminho=None):
    if caminho is None:
        caminho = path("caso_nacional", "oferta_s.csv")
    s_df = pd.DataFrame(
        [
            {"municipio": municipio, "vacina": vacina, "oferta": oferta}
            for (municipio, vacina), oferta in s.items()
        ]
    )
    s_df.to_csv(caminho, index=False)


def calcular_s_sem_intermediacao_estadual():
    s = {}
    for j in J:
        for v in V:
            oferta_inicial = o.get((j, v), 0)

            # saída: j -> destino
            saida_jv = sum(
                x_ijv[j, destino, v].X for destino in J if j != destino)

            # entrada: origem -> j
            entrada_jv = sum(
                x_ijv[origem, j, v].X for origem in I if origem != j)

            s[j, v] = int(round(oferta_inicial - saida_jv + entrada_jv))

    return s


def calcular_s_com_intermediacao_estadual():
    s = {}
    for j in J:
        for v in V:
            oferta_inicial = o.get((j, v), 0)

            # saída: j -> k
            saida_jv = sum(x_ikv[j, k, v].X for k in K if j in I)

            # entrada: k -> j
            entrada_jv = sum(y_kjv[k, j, v].X for k in K)

            s[j, v] = int(round(oferta_inicial - saida_jv + entrada_jv))

    return s


# Exporta tambem o s inicial derivado de o.
exportar_oferta_s_csv(s)

# Tempo até expiração (horas) - etapa intermunicipal
expiracao_iv_df = pd.read_csv(
    path("caso_nacional", "expiracao_iv.csv"), usecols=[0, 1, 2]).dropna()
col_municipio = expiracao_iv_df.columns[0]
col_vacina = expiracao_iv_df.columns[1]
col_tempo = expiracao_iv_df.columns[2]

e_iv = {
    (str(linha[col_municipio]), str(linha[col_vacina])): float(linha[col_tempo])
    for _, linha in expiracao_iv_df.iterrows()
}

# Tempo até expiração (horas) - etapa intramunicipal
# Hipotese de agregacao: as vacinas de um mesmo tipo recebidas em j
# sao tratadas como um estoque unico, sem rastrear lote/origem.
# Assim, e_jv[j, v] representa uma validade remanescente agregada
# (media ponderada ou aproximacao conservadora dos fluxos recebidos).

e_jv = dict(e_iv)


def exportar_expiracao_jv_csv(e_jv, caminho=None):
    if caminho is None:
        caminho = path("caso_nacional", "expiracao_jv.csv")
    e_jv_df = pd.DataFrame(
        [
            {"municipio": municipio, "vacina": vacina, "tempo_expiracao": tempo}
            for (municipio, vacina), tempo in e_jv.items()
        ]
    )
    e_jv_df.to_csv(caminho, index=False)


def calcular_e_jv_sem_intermediacao_estadual():
    e_jv_calc = {}

    for j in J:
        for v in V:
            numerador = 0.0
            denominador = 0.0

            for i in I:
                o_iv = o.get((i, v), 0)
                e_iv_val = e_iv.get((i, v), 0)
                C_ij_val = C_ij.get((i, j), 0)

                numerador += o_iv * (e_iv_val - C_ij_val)
                denominador += o_iv

            # proteção contra divisão por zero
            if denominador > 0:
                e_jv_calc[j, v] = numerador / denominador
            else:
                e_jv_calc[j, v] = 0

    return e_jv_calc


def calcular_e_jv_com_intermediacao_estadual():
    e_kv = {}

    # =========================
    # ETAPA 1: calcular e_kv
    # =========================
    for k in K:
        for v in V:
            numerador = 0.0
            denominador = 0.0

            for i in I:
                o_iv = o.get((i, v), 0)
                e_iv_val = e_iv.get((i, v), 0)
                C_ik_val = C_ik.get((i, k), 0)

                numerador += o_iv * (e_iv_val - C_ik_val)
                denominador += o_iv

            if denominador > 0:
                e_kv[k, v] = numerador / denominador
            else:
                e_kv[k, v] = 0

    # =========================
    # ETAPA 2: calcular e_jv
    # =========================
    e_jv = {}

    for j in J:
        for v in V:
            numerador = 0.0
            denominador = 0.0

            for k in K:
                C_kj_val = C_kj.get((k, j), 0)

                # proteção contra divisão por zero
                if C_kj_val == 0:
                    C_kj_val = 0.001

                peso = 1.0 / C_kj_val
                e_kv_val = e_kv.get((k, v), 0)

                numerador += peso * (e_kv_val - C_kj_val)
                denominador += peso

            if denominador > 0:
                e_jv[j, v] = numerador / denominador
            else:
                e_jv[j, v] = 0

    return e_jv


exportar_expiracao_jv_csv(e_jv)

# penalidade (0 quando o tempo de expiracao nao e definido)
rho_iv = {
    (i, v): (1 / e_iv[i, v]) if e_iv.get((i, v), 0) > 0 else 0
    for i in I for v in V
}

rho_jv = {
    (j, v): (1 / e_jv[j, v]) if e_jv.get((j, v), 0) > 0 else 0
    for j in J for v in V
}

U_ROD = int(constantes["U_ROD"])   # Capacidade por modal (doses)
U_AEREO = int(constantes["U_AEREO"])  # Capacidade por modal (doses)


# ===================================
# PARÂMETROS DE CUSTO E TEMPO
# ===================================

municipios_coordenadas = {
    row["municipio"]: (float(row["latitude"]), float(row["longitude"]))
    for _, row in M_df.iterrows()
}

estados_coordenadas = {
    row["estado"]: (float(row["latitude"]), float(row["longitude"]))
    for _, row in estados_df.iterrows()
}

postos_coordenadas = {
    str(row[col_posto]): (float(row["latitude"]), float(row["longitude"]))
    for _, row in postos_df.iterrows()
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


beta = float(constantes["beta"])
theta_rod = float(constantes["theta_rod"])
theta_aereo = float(constantes["theta_aereo"])

# tempo de deslocamento do modal rodoviario (horas) do municipio i para o municipio j
tau_ij_rod = {}
# tempo de deslocamento do modal aereo (horas) do municipio i para o municipio j
tau_ij_aereo = {}

for i in I:
    for j in J:
        if i == j:
            tau_ij_rod[i, j] = 0
            tau_ij_aereo[i, j] = 0
        else:
            lat1, lon1 = municipios_coordenadas[i]
            lat2, lon2 = municipios_coordenadas[j]

            Delta_ij = haversine(lat1, lon1, lat2, lon2)

            tau_ij_rod[i, j] = (beta * Delta_ij) / theta_rod
            tau_ij_aereo[i, j] = Delta_ij / theta_aereo


# tempo de deslocamento do modal rodoviario (horas) do municipio i para o estado k
tau_ik_rod = {}
# tempo de deslocamento do modal aereo (horas) do municipio i para o estado k
tau_ik_aereo = {}

for i in I:
    for k in K:
        lat1, lon1 = municipios_coordenadas[i]
        lat2, lon2 = estados_coordenadas[k]

        Delta_ik = haversine(lat1, lon1, lat2, lon2)

        tau_ik_rod[i, k] = (beta * Delta_ik) / theta_rod
        tau_ik_aereo[i, k] = Delta_ik / theta_aereo


# tempo de deslocamento do modal rodoviario (horas) do estado k para o municipio j
tau_kj_rod = {}
# tempo de deslocamento do modal aereo (horas) do estado k para o municipio j
tau_kj_aereo = {}

for k in K:
    for j in J:
        lat1, lon1 = estados_coordenadas[k]
        lat2, lon2 = municipios_coordenadas[j]

        Delta_kj = haversine(lat1, lon1, lat2, lon2)

        tau_kj_rod[k, j] = (beta * Delta_kj) / theta_rod
        tau_kj_aereo[k, j] = Delta_kj / theta_aereo


# tempo de deslocamento do modal rodoviario (horas) do municipio j para o posto p
tau_jp_rod = {}

for j in J:
    for p in P[j]:
        lat1, lon1 = municipios_coordenadas[j]
        lat2, lon2 = postos_coordenadas[p]

        Delta_jp = haversine(lat1, lon1, lat2, lon2)

        tau_jp_rod[j, p] = (beta * Delta_jp) / theta_rod

# Tempo de processamento logístico (horas)
# tempo de processamento logístico para descarga e conferência simples
delta_rod = float(constantes["delta_rod"])
# tempo de processamento logístico para desembarque, inspeção, logística aeroportuária
delta_aereo = float(constantes["delta_aereo"])

# fator de penalização para transporte aéreo (custo adicional devido a complexidade e risco)
alpha = float(constantes["alpha"])

# Custos (tempo em horas)


def custo_rod(tau, delta):
    return tau + delta


def custo_aereo(tau, delta):
    return alpha * tau + delta


def custo_final(c_rod, c_aereo):
    return min(c_rod, c_aereo)


C_ij_rod = {}
C_ij_aereo = {}
C_ij = {}

for i in I:
    for j in J:

        # caso trivial: mesmo município
        if i == j:
            C_ij_rod[i, j] = 0.0
            C_ij_aereo[i, j] = 0.0
            C_ij[i, j] = 0.0
            continue

        # custo rodoviário
        C_ij_rod[i, j] = custo_rod(tau_ij_rod[i, j], delta_rod)

        # custo aéreo
        C_ij_aereo[i, j] = custo_aereo(tau_ij_aereo[i, j], delta_aereo)

        # custo final (escolha do melhor modal)
        C_ij[i, j] = custo_final(C_ij_rod[i, j], C_ij_aereo[i, j])


C_ik_rod = {}
C_ik_aereo = {}
C_ik = {}

for i in I:
    for k in K:
        # custo rodoviário
        C_ik_rod[i, k] = custo_rod(tau_ik_rod[i, k], delta_rod)

        # custo aéreo
        C_ik_aereo[i, k] = custo_aereo(tau_ik_aereo[i, k], delta_aereo)

        # custo final (escolha do melhor modal)
        C_ik[i, k] = custo_final(C_ik_rod[i, k], C_ik_aereo[i, k])


C_kj_rod = {}
C_kj_aereo = {}
C_kj = {}

for k in K:
    for j in J:
        # custo rodoviário
        C_kj_rod[k, j] = custo_rod(tau_kj_rod[k, j], delta_rod)

        # custo aéreo
        C_kj_aereo[k, j] = custo_aereo(tau_kj_aereo[k, j], delta_aereo)

        # custo final (escolha do melhor modal)
        C_kj[k, j] = custo_final(C_kj_rod[k, j], C_kj_aereo[k, j])

C_jp = {}

for j in J:
    for p in P[j]:
        C_jp[j, p] = custo_rod(tau_jp_rod[j, p], delta_rod)

# Capacidade efetiva alinhada ao modal de menor custo em cada arco.
u_ij = {
    (i, j): (W_rod_mun.get(i, 0) * U_ROD)
    if C_ij_rod[i, j] <= C_ij_aereo[i, j]
    else (W_aereo_mun.get(i, 0) * U_AEREO)
    for i in I for j in J
}

u_ik = {
    (i, k): (W_rod_mun.get(i, 0) * U_ROD)
    if C_ik_rod[i, k] <= C_ik_aereo[i, k]
    else (W_aereo_mun.get(i, 0) * U_AEREO)
    for i in I for k in K
}

u_kj = {
    (k, j): (W_rod_estado.get(k, 0) * U_ROD)
    if C_kj_rod[k, j] <= C_kj_aereo[k, j]
    else (W_aereo_estado.get(k, 0) * U_AEREO)
    for k in K for j in J
}

# q = u sempre se considerarmos transporte rodoviario
q = {(j, p): W_rod_mun.get(j, 0) * U_ROD for j in J for p in P[j]}

# Mantem a mesma interface das restricoes ja implementadas.
u = {}
u.update(u_ij)
u.update(u_ik)
u.update(u_kj)

# =========================
# Variáveis de decisão e Objeto Modelo
# =========================
m = Model("distribuicao_vacinas")

x_ijv = m.addVars(
    [(i, j, v) for i in I for j in J if i != j for v in V],
    vtype=GRB.INTEGER,
    name="x_ijv",
    lb=0
)  # inteira e não negativa

x_ikv = m.addVars(I, K, V, vtype=GRB.INTEGER, name="x_ikv",
                  lb=0)  # inteira e não negativa

y_kjv = m.addVars(K, J, V, vtype=GRB.INTEGER, name="y_kjv",
                  lb=0)  # inteira e não negativa

z = m.addVars(
    [(j, p, v) for j in J for p in P[j] for v in V],
    vtype=GRB.INTEGER,
    name="z",
    lb=0
)  # inteira e não negativa

# variáveis de falta

# f_jv: quantidade de vacinas v que faltou no município j
f_jv = m.addVars(
    [(j, v) for j in J for v in V],
    vtype=GRB.INTEGER,
    name="f_jv",
    lb=0
)


# f_pv: quantidade de vacinas v que faltou no posto p do município j
f_pv = m.addVars(
    [(p, v) for j in J for p in P[j] for v in V],
    vtype=GRB.INTEGER,
    name="f_pv",
    lb=0
)

# Penalidade de Falta, o valor deve ser muito maior que qualquer tempo de transporte do modelo
M = 100000 


# =========================
# MODELOS
# =========================

# modelo intermunicipal sem_intermediacao_estadual
def modelo_intermunicipal_sem_intermediacao_estadual(penalidade, rho_iv):

    if penalidade == True:
        # função objetivo
        m.setObjective(
            quicksum(C_ij[i, j] * x_ijv[i, j, v] * rho_iv[i, v]
                     for i in I for j in J if i != j for v in V)
            + quicksum(M * f_jv[j, v] 
                       for j in J for v in V),
            GRB.MINIMIZE
        )
    else:
        # função objetivo
        m.setObjective(
            quicksum(C_ij[i, j] * x_ijv[i, j, v]
                     for i in I for j in J if i != j for v in V)
            + quicksum(M * f_jv[j, v]
               for j in J for v in V),
            GRB.MINIMIZE
        )

    # restrições
    # oferta
    for i in I:
        for v in V:
            m.addConstr(
                quicksum(x_ijv[i, j, v] for j in J if i != j) <= o[i, v]
            )

    # demanda
    for j in J:
        for v in V:
            m.addConstr(
                quicksum(x_ijv[i, j, v] for i in I if i != j)
                + f_jv[j, v] == d[j, v]
            )

    # capacidade
    for i in I:
        for j in J:
            if i != j:
                m.addConstr(
                    quicksum(x_ijv[i, j, v] for v in V) <= u[i, j]
                )

    # viabilidade temporal
    for (i, j, v) in x_ijv.keys():
        if C_ij[i, j] > e_iv[i, v]:
            m.addConstr(
                x_ijv[i, j, v] == 0
            )

    m.optimize()

    # tempo de distribuição
    if m.status == GRB.OPTIMAL:

        T = 0.0
        rota = None
        doses = 0
        vacina = None

        for i in I:
            for j in J:
                if i != j:
                    for v in V:
                        if x_ijv[i, j, v].X > 0:
                            tempo_total = C_ij[i, j]
                            if tempo_total > T:
                                T = tempo_total
                                rota = f"{i} -> {j}"
                                doses = int(x_ijv[i, j, v].X)
                                vacina = v

        doses_faltantes = sum(f_jv[j, v].X for j in J for v in V)

        return {
            "T": T,
            "rota": rota,
            "doses": doses,
            "vacina": vacina,
            "doses_faltantes": doses_faltantes
        }

    return None

# modelo intermunicipal com_intermediacao_estadual


def modelo_intermunicipal_com_intermediacao_estadual(penalidade, rho_iv):

    if penalidade == True:
        # função objetivo
        m.setObjective(
            quicksum(C_ik[i, k] * x_ikv[i, k, v] * rho_iv[i, v] 
                     for k in K for i in I for v in V) +
            quicksum(C_kj[k, j] * y_kjv[k, j, v]
                     for j in J for k in K for v in V) +
            quicksum(M * f_jv[j, v]
                for j in J for v in V),
            GRB.MINIMIZE
        )
    else:
        # funçao objetivo
        m.setObjective(
            quicksum(C_ik[i, k] * x_ikv[i, k, v] 
                     for k in K for i in I for v in V) +
            quicksum(C_kj[k, j] * y_kjv[k, j, v]
                     for j in J for k in K for v in V) +
            quicksum(M * f_jv[j, v]
                for j in J for v in V),
            GRB.MINIMIZE
        )

    # restrições

    # Restriçãode Consistencia Estadual: um estado k só pode enviar para o municipio j se j pertence a k.
    for k in K:
        for j in J:
            # Se o estado do centro k for diferente do estado do município j
            if estado_de.get(k) != estado_de.get(j):
                m.addConstr(
                    quicksum(y_kjv[k, j, v] for v in V) == 0,
                    name=f"consistencia_estadual_{k}_{j}"
                )

    # oferta
    for i in I:
        for v in V:
            m.addConstr(
                quicksum(x_ikv[i, k, v] for k in K) <= o[i, v]
            )

    # demanda
    for j in J:
        for v in V:
            m.addConstr(
                quicksum(y_kjv[k, j, v] for k in K) + f_jv[j, v] == d[j, v]
            )

    # conservacao do fluxo
    # "Tudo o que a rede inteira precisa ($y$) tem que ser igual a tudo o que a rede inteira envia para o estado ($x$)."
    for k in K:
        for v in V:
            m.addConstr(
                quicksum(x_ikv[i, k, v]
                         for i in I) == quicksum(y_kjv[k, j, v] for j in J)
            )

    # primeira restrição de capacidade
    for i in I:
        for k in K:
            m.addConstr(
                quicksum(x_ikv[i, k, v] for v in V) <= u[i, k]
            )

    # segunda restrição de capacidade
    for k in K:
        for j in J:
            m.addConstr(
                quicksum(y_kjv[k, j, v] for v in V) <= u[k, j]
            )

    # viabilidade temporal
    for (i, k, v) in x_ikv.keys():
        if C_ik[i, k] > e_iv[i, v]:
            m.addConstr(
                x_ikv[i, k, v] == 0
            )

    m.optimize()

    # tempo de distribuição
    if m.status == GRB.OPTIMAL:

        L = 0.0
        L1 = L2 = 0.0
        rota_l1 = rota_l2 = None
        doses_l1 = doses_l2 = 0
        vacina = None

        for i in I:
            for k in K:
                for v in V:
                    # Cláusula de guarda: se não saiu de i para k, pula
                    if x_ikv[i, k, v].X <= 0:
                        continue

                    for j in J:
                        if y_kjv[k, j, v].X <= 0:
                            continue

                        tempo_total = (C_ik[i, k] + C_kj[k, j])

                        if tempo_total > L:
                            L = tempo_total

                            L1 = C_ik[i, k]
                            L2 = C_kj[k, j]

                            rota_l1 = f"{i} -> {k}"
                            rota_l2 = f"{k} -> {j}"

                            doses_l1 = int(x_ikv[i, k, v].X)
                            doses_l2 = int(y_kjv[k, j, v].X)

                            vacina = v

        doses_faltantes = sum(f_jv[j, v].X for j in J for v in V)

        return {
            "L": L,
            "L1": L1, "rota_l1": rota_l1, "doses_l1": doses_l1,
            "L2": L2, "rota_l2": rota_l2, "doses_l2": doses_l2,
            "vacina": vacina,
            "doses_faltantes": doses_faltantes
        }

    return None

# modelo intramunicipal


def modelo_intramunicipal(penalidade, rho_jv):

    if penalidade == True:
        # função objetivo
        m.setObjective(
            quicksum(C_jp[j, p] * z[j, p, v] * rho_jv[j, v]
                     for j in J for p in P[j] for v in V)
            + quicksum(M * f_pv[p, v] for j in J for p in P[j] for v in V),
            GRB.MINIMIZE
        )
    else:
        # função objetivo
        m.setObjective(
            quicksum(C_jp[j, p] * z[j, p, v]
                     for j in J for p in P[j] for v in V)
            + quicksum(M * f_pv[p, v] for j in J for p in P[j] for v in V),
            GRB.MINIMIZE
        )

    # restrições

    # oferta
    for j in J:
        for v in V:
            m .addConstr(
                quicksum(z[j, p, v] for p in P[j]) <= s[j, v]
            )

    # demanda
    for j in J:
        for p in P[j]:
            for v in V:
                m.addConstr(
                    z[j, p, v] + f_pv[p, v] == r[p, v]
                )

    # capacidade
    for j in J:
        for p in P[j]:
            m.addConstr(
                quicksum(z[j, p, v] for v in V) <= q[j, p]
            )

    # viabilidade temporal
    for (j, p, v) in z.keys():
        if C_jp[j, p] > e_jv.get((j, v), float("inf")):
            m.addConstr(
                z[j, p, v] == 0
            )

    m.optimize()

    # tempo de distribuição
    if m.status == GRB.OPTIMAL:

        T = 0.0
        rota = None
        doses = 0
        vacina = None

        for j in J:
            for p in P[j]:
                for v in V:
                    if z[j, p, v].X > 0:
                        tempo_total = C_jp[j, p]
                        if tempo_total > T:
                            T = tempo_total
                            rota = f"{j} -> {p}"
                            doses = int(z[j, p, v].X)
                            vacina = v
        
        doses_faltantes = sum(f_pv[p, v].X for j in J for p in P[j] for v in V)

        return {
            "T": T,
            "rota": rota,
            "doses": doses,
            "vacina": vacina,
            "doses_faltantes": doses_faltantes # Retorna o total de vacinas que faltaram
        }

    return None

# modelo unificado sem_intermediacao_estadual


def modelo_unificado_sem_intermediacao_estadual(penalidade, rho_iv, rho_jv):
    # função objetivo
    if penalidade == True:
        m.setObjective(
            quicksum(C_ij[i, j] * x_ijv[i, j, v] * rho_iv[i, v]
                     for i in I for j in J if i != j for v in V) +
            quicksum(C_jp[j, p] * z[j, p, v] * rho_jv[j, v]
                     for j in J for p in P[j] for v in V) +
            quicksum(M * f_pv[p, v]
                     for j in J for p in P[j] for v in V
            ),
            GRB.MINIMIZE
        )
    else:
        m.setObjective(
            quicksum(C_ij[i, j] * x_ijv[i, j, v]
                     for i in I for j in J if i != j for v in V) +
            quicksum(C_jp[j, p] * z[j, p, v]
                     for j in J for p in P[j] for v in V) +
            quicksum(M * f_pv[p, v]
                     for j in J for p in P[j] for v in V
            )         ,
            GRB.MINIMIZE
        )

    # =========================
    # RESTRIÇÕES INTERMUNICIPAIS
    # =========================

    # oferta
    for i in I:
        for v in V:
            m.addConstr(
                quicksum(x_ijv[i, j, v] for j in J if i != j) <= o[i, v]
            )

    # demanda
    # no modelo unificado, a demanda final e imposta no nivel dos postos (z)

    # capacidade
    for i in I:
        for j in J:
            if i != j:
                m.addConstr(
                    quicksum(x_ijv[i, j, v] for v in V) <= u[i, j]
                )

    # viabilidade temporal
    for (i, j, v) in x_ijv.keys():
        if C_ij[i, j] > e_iv[i, v]:
            m.addConstr(
                x_ijv[i, j, v] == 0
            )

    # =========================
    # RESTRIÇÕES INTRAMUNICIPAIS
    # =========================

    # oferta
    # já está incorporado na nova restrição de disponibilidade.

    # demanda
    for j in J:
        for p in P[j]:
            for v in V:
                m.addConstr(
                    z[j, p, v] + f_pv[p, v] == r[p, v]
                )

    # capacidade
    for j in J:
        for p in P[j]:
            m.addConstr(
                quicksum(z[j, p, v] for v in V) <= q[j, p]
            )

    # viabilidade temporal
    for (j, p, v) in z.keys():
        if C_jp[j, p] > e_jv.get((j, v), float("inf")):
            m.addConstr(
                z[j, p, v] == 0
            )

    # Restrição de Conservação e Disponibilidade de Fluxo
    for j in J:
        for v in V:
            m.addConstr(
                # O que sai: Doses enviadas aos postos locais + Doses enviadas para outras cidades
                quicksum(z[j, p, v] for p in P[j]) +
                quicksum(x_ijv[j, destino, v] for destino in J if destino != j)
                <=
                # O que se tem: Estoque inicial na cidade + Doses recebidas de outras cidades
                o.get((j, v), 0) +
                quicksum(x_ijv[origem, j, v] for origem in I if origem != j),
                name=f"balanco_fluxo_{j}_{v}"
            )

    # otimização
    m.optimize()

    # =========================
    # TEMPO DE DISTRIBUIÇÃO
    # =========================

    if m.status == GRB.OPTIMAL:

        L = 0.0
        L1 = L2 = 0.0

        rota_l1 = rota_l2 = None
        doses_l1 = doses_l2 = 0
        vacina = None

        # -------------------------
        # 1. CASO LOCAL (sem intermunicipal)
        # -------------------------
        for j in J:
            for p in P[j]:
                for v in V:
                    if z[j, p, v].X > 0:
                        tempo_local = C_jp[j, p]

                        if tempo_local > L:
                            L = tempo_local

                            L1 = 0.0
                            L2 = tempo_local

                            rota_l1 = "N/A"
                            rota_l2 = f"{j} -> {p}"

                            doses_l1 = 0
                            doses_l2 = int(z[j, p, v].X)

                            vacina = v

        # -------------------------
        # 2. CAMINHO COMPLETO (com intermunicipal)
        # -------------------------
        for i in I:
            for j in J:
                if i != j:
                    for v in V:
                        if x_ijv[i, j, v].X <= 0:
                            continue

                        for p in P[j]:
                            if z[j, p, v].X <= 0:
                                continue

                            tempo_total = C_ij[i, j] + C_jp[j, p]

                            if tempo_total > L:
                                L = tempo_total

                                L1 = C_ij[i, j]
                                L2 = C_jp[j, p]

                                rota_l1 = f"{i} -> {j}"
                                rota_l2 = f"{j} -> {p}"

                                doses_l1 = int(x_ijv[i, j, v].X)
                                doses_l2 = int(z[j, p, v].X)

                                vacina = v

        doses_faltantes = sum(f_pv[p, v].X for j in J for p in P[j] for v in V)

        return {
            "L": L,
            "L1": L1, "rota_l1": rota_l1, "doses_l1": doses_l1,
            "L2": L2, "rota_l2": rota_l2, "doses_l2": doses_l2,
            "vacina": vacina,
            "doses_faltantes": doses_faltantes
        }

    return None

# modelo unificado com_intermediacao_estadual
def modelo_unificado_com_intermediacao_estadual(penalidade, rho_iv, rho_jv):

    # funcao objetivo
    if penalidade == True:
        m.setObjective(
            quicksum(C_ik[i, k] * x_ikv[i, k, v] * rho_iv[i, v] 
                     for k in K for i in I for v in V) +
            quicksum(C_kj[k, j] * y_kjv[k, j, v] 
                     for j in J for k in K for v in V) +
            quicksum(C_jp[j, p] * z[j, p, v] * rho_jv[j, v]
                     for j in J for p in P[j] for v in V) +
            quicksum(M * f_pv[p, v]
                     for j in J for p in P[j] for v in V),
            GRB.MINIMIZE
        )

    else:
        m.setObjective(
            quicksum(C_ik[i, k] * x_ikv[i, k, v] 
                     for k in K for i in I for v in V) +
            quicksum(C_kj[k, j] * y_kjv[k, j, v] 
                     for j in J for k in K for v in V) +
            quicksum(C_jp[j, p] * z[j, p, v] 
                     for j in J for p in P[j] for v in V) +
            quicksum(M * f_pv[p, v] 
                     for j in J for p in P[j] for v in V),
            GRB.MINIMIZE
        )

    # Restrição de Consistencia Estadual: um estado k só pode enviar para o municipio j se j pertence a k.
    for k in K:
        for j in J:
            # Se o estado do centro k for diferente do estado do município j
            if estado_de.get(k) != estado_de.get(j):
                m.addConstr(
                    quicksum(y_kjv[k, j, v] for v in V) == 0,
                    name=f"consistencia_estadual_{k}_{j}"
                )

    # restrições intermunicipais
    # oferta
    for i in I:
        for v in V:
            m.addConstr(
                quicksum(x_ikv[i, k, v] for k in K) <= o[i, v]
            )

    # demanda
    # no modelo unificado, a demanda final e imposta no nivel dos postos (z)

    # conservacao do fluxo
    for k in K:
        for v in V:
            m.addConstr(
                quicksum(x_ikv[i, k, v]
                         for i in I) == quicksum(y_kjv[k, j, v] for j in J)
            )

    # primeira restrição de capacidade
    for i in I:
        for k in K:
            m.addConstr(
                quicksum(x_ikv[i, k, v] for v in V) <= u[i, k]
            )

    # segunda restrição de capacidade
    for k in K:
        for j in J:
            m.addConstr(
                quicksum(y_kjv[k, j, v] for v in V) <= u[k, j]
            )

    # viabilidade temporal
    for (i, k, v) in x_ikv.keys():
        if C_ik[i, k] > e_iv[i, v]:
            m.addConstr(
                x_ikv[i, k, v] == 0
            )

    # restrições intramunicipais

    # oferta
    # já está incorporado na nova restrição de disponibilidade

    # demanda
    for j in J:
        for p in P[j]:
            for v in V:
                m.addConstr(
                    z[j, p, v] + f_pv[p, v] == r[p, v]
                )

    # capacidade
    for j in J:
        for p in P[j]:
            m.addConstr(
                quicksum(z[j, p, v] for v in V) <= q[j, p]
            )

    # viabilidade temporal
    for (j, p, v) in z.keys():
        if C_jp[j, p] > e_jv.get((j, v), float("inf")):
            m.addConstr(
                z[j, p, v] == 0
            )

    # Restrição de Conservação e Disponibilidade de Fluxo
    for j in J:
        for v in V:
            m.addConstr(
                # O que sai: Doses enviadas aos postos locais + Doses enviadas ao estado
                quicksum(z[j, p, v] for p in P[j]) +
                quicksum(x_ikv[j, k, v] for k in K if j in I)
                <=
                # O que se tem: Estoque inicial na cidade + Doses recebidas do estado
                o.get((j, v), 0) +
                quicksum(y_kjv[k, j, v] for k in K),
                name=f"balanco_fluxo_estadual_{j}_{v}"
            )

    m.optimize()

    if m.status == GRB.OPTIMAL:

        L = 0.0
        L1 = L2 = L3 = 0.0

        rota_l1 = rota_l2 = rota_l3 = None
        doses_l1 = doses_l2 = doses_l3 = 0
        vacina = None

        # -------------------------
        # 1. CASO LOCAL (sem intermunicipal)
        # -------------------------
        for j in J:
            for p in P[j]:
                for v in V:
                    if z[j, p, v].X > 0:
                        tempo_local = C_jp[j, p]

                        if tempo_local > L:
                            L = tempo_local

                            L1 = 0.0
                            L2 = 0.0
                            L3 = tempo_local

                            rota_l1 = "N/A"
                            rota_l2 = "N/A"
                            rota_l3 = f"{j} -> {p}"

                            doses_l1 = 0
                            doses_l2 = 0
                            doses_l3 = int(z[j, p, v].X)

                            vacina = v

        # -------------------------
        # 2. CAMINHO COMPLETO
        # -------------------------
        for i in I:
            for k in K:
                for v in V:
                    if x_ikv[i, k, v].X <= 0:
                        continue

                    for j in J:
                        if y_kjv[k, j, v].X <= 0:
                            continue

                        for p in P[j]:
                            if z[j, p, v].X <= 0:
                                continue

                            tempo_total = (
                                C_ik[i, k] +
                                C_kj[k, j] +
                                C_jp[j, p]
                            )

                            if tempo_total > L:
                                L = tempo_total

                                L1 = C_ik[i, k]
                                L2 = C_kj[k, j]
                                L3 = C_jp[j, p]

                                rota_l1 = f"{i} -> {k}"
                                rota_l2 = f"{k} -> {j}"
                                rota_l3 = f"{j} -> {p}"

                                doses_l1 = int(x_ikv[i, k, v].X)
                                doses_l2 = int(y_kjv[k, j, v].X)
                                doses_l3 = int(z[j, p, v].X)

                                vacina = v

         # total de doses faltantes
        doses_faltantes = sum(int(f_pv[p, v].X) for j in J for p in P[j] for v in V)

        return {
            "L": L,
            "L1": L1, "rota_l1": rota_l1, "doses_l1": doses_l1,
            "L2": L2, "rota_l2": rota_l2, "doses_l2": doses_l2,
            "L3": L3, "rota_l3": rota_l3, "doses_l3": doses_l3,
            "vacina": vacina,
            "doses_faltantes": doses_faltantes
        }

    return None


def exportar_detalhes(modelo, penalidade, lista_rotas):
    """Gera o CSV individual de cada execução (1 de 10 possíveis)."""
    resultados_dir = path("resultados_caso_nacional")
    if not os.path.exists(resultados_dir):
        os.makedirs(resultados_dir)

    df = pd.DataFrame(lista_rotas)
    nome_arq = path(
        "resultados_caso_nacional",
        f"detalhes_mod{modelo}_pen{str(penalidade).upper()}.csv"
    )
    df.to_csv(nome_arq, index=False)
    print(f"\n-> Arquivo de detalhes gerado: {nome_arq}")


def registrar_comparativo(resumo):
    """Anexa os resultados globais ao CSV de comparação (o 11º arquivo)."""
    caminho = path("resultados_caso_nacional", "comparativo_modelos.csv")
    df_novo = pd.DataFrame([resumo])

    # Se o arquivo não existe, cria com cabeçalho. Se existe, anexa.
    header = not os.path.exists(caminho)
    df_novo.to_csv(caminho, mode='a', index=False, header=header)
    print(f"-> Registro adicionado ao comparativo global.")


def executar_modelomodelo_intermunicipal_sem_intermediacao_estadual(penalidade, rho_iv):

    resultado = modelo_intermunicipal_sem_intermediacao_estadual(
        penalidade, rho_iv)

    if m.status == GRB.OPTIMAL:

        rotas_list = []
        total_doses = 0
        soma_custo_ponderado = 0.0
        custo_medio_horas_por_dose = 0.0
        modal_aereo = 0
        modal_rodoviario = 0
        percenteual_aereo = 0.0
        percentual_rodoviario = 0.0

        print("\n" + "="*60)
        print(" RELATÓRIO DE DISTRIBUIÇÃO")
        print("="*60)
        print()

        for i in I:
            for j in J:
                if i != j:
                    for v in V:
                        if x_ijv[i, j, v].X > 0:

                            doses = int(x_ijv[i, j, v].X)
                            custo_final = min(C_ij_rod[i, j], C_ij_aereo[i, j])
                            modal = "rodoviario" if custo_final == C_ij_rod[i,
                                                                            j] else "aereo"

                            # Dados para o CSV de Detalhes do Modelo
                            rotas_list.append({
                                "Modelo": 1, "Usa_Penalidade": penalidade,
                                "ID_Fluxo": f"{i}_{j}_{v}", "Rota": f"{i} -> {j}", "Tipo_Rota": "ij",
                                "Origem": i, "Lat_O": municipios_coordenadas[i][0], "Lon_O": municipios_coordenadas[i][1],
                                "Destino": j, "Lat_D": municipios_coordenadas[j][0], "Lon_D": municipios_coordenadas[j][1],
                                "Custo_Horas": f"{custo_final:.3f}",
                                "Doses": doses,
                                "Vacina": v,
                                "Modal": modal,
                            })

                            # Acumuladores para Comparativo entre Modelos
                            total_doses += doses
                            soma_custo_ponderado += (doses * custo_final)
                            if modal == "rodoviario":
                                modal_rodoviario += 1
                            else:
                                modal_aereo += 1

                            print(
                                f"De {i} para {j} em {custo_final:.3f} hora(s) | {v}: {doses} doses (Modal: {modal})")

        print("\n" + "-"*60)
        print(" MÉTRICAS GLOBAIS DE DESEMPENHO")
        print("-"*60)

        custo_medio_horas_por_dose = soma_custo_ponderado / \
            total_doses if total_doses > 0 else 0
        percentual_rodoviario = (modal_rodoviario / (modal_rodoviario + modal_aereo)
                                 ) * 100 if (modal_rodoviario + modal_aereo) > 0 else 0
        percenteual_aereo = (modal_aereo / (modal_rodoviario + modal_aereo)) * \
            100 if (modal_rodoviario + modal_aereo) > 0 else 0

        # 3. Dados do CSV para Registro do Comparativo
        resumo = {
            "Modelo": 1,
            "Usa_Penalidade": penalidade,
            "Custo_Medio_Horas_Por_Dose": f"{custo_medio_horas_por_dose:.3f}",
            "Valor_Funcao_Objetivo": f"{m.ObjVal:.3f}",
            "Total_Doses_Movimentadas": total_doses,
            "Total_Doses_Faltantes": resultado['doses_faltantes'],
            "Percentual_Rodoviario": f"{percentual_rodoviario:.3f}",
            "Percentual_Aereo": f"{percenteual_aereo:.3f}",
            "Tempo_Maximo": f"{resultado['T']:.3f}",
            "Vacina_Maior_Tempo": resultado['vacina'],
            "Rota_Gargalo_ij": resultado['rota'],
            "Tempo_Rota_Gargalo_ij": f"{resultado['T']:.3f}",
            "Doses_Rota_Gargalo_ij": resultado['doses'],
            "Rota_Gargalo_ik": "N/A",
            "Tempo_Rota_Gargalo_ik": "N/A",
            "Doses_Rota_Gargalo_ik": "N/A",
            "Rota_Gargalo_kj": "N/A",
            "Tempo_Rota_Gargalo_kj": "N/A",
            "Doses_Rota_Gargalo_kj": "N/A",
            "Rota_Gargalo_jp": "N/A",
            "Tempo_Rota_Gargalo_jp": "N/A",
            "Doses_Rota_Gargalo_jp": "N/A"
        }

        # Dados impressos no termimal
        print(f"\nTotal de doses movimentadas: {total_doses}")
        print(f"\nTotal de doses que não atenderam a demanda: {resultado['doses_faltantes']}")
        print(f"\nTempo Total de Distribuicao: {resultado['T']:.3f} horas")
        print(f"\nVacina do maior tempo: {resultado['vacina']}")
        print(
            f"\nGargalo Etapa ij - {resultado['T']:.3f} horas (Rota: {resultado['rota']} ({resultado['doses']} doses))")

        print("-"*60)

        # Exportação dos Detalhes do Modelo 1
        exportar_detalhes(1, penalidade, rotas_list)
        registrar_comparativo(resumo)
        print("Processamento do Modelo 1 concluído com sucesso.")

        print("="*60 + "\n")

    else:
        print("Modelo inviavel ou sem solçao otima.")


def executar_modelomodelo_intermunicipal_com_intermediacao_estadual(penalidade, rho_iv):

    resultado = modelo_intermunicipal_com_intermediacao_estadual(
        penalidade, rho_iv)

    if m.status == GRB.OPTIMAL:

        rotas_list = []
        total_doses = 0
        soma_custo_ponderado = 0.0
        custo_medio_horas_por_dose = 0.0
        modal_aereo = 0
        modal_rodoviario = 0
        percenteual_aereo = 0.0
        percentual_rodoviario = 0.0

        print("\n" + "="*60)
        print(" RELATÓRIO DE DISTRIBUIÇÃO")
        print("="*60)

        print("\n[ETAPA 1] Município I -> Centro Estadual K")
        for i in I:
            for k in K:
                for v in V:
                    if x_ikv[i, k, v].X > 0:
                        doses = int(x_ikv[i, k, v].X)
                        custo_final = min(C_ik_rod[i, k], C_ik_aereo[i, k])
                        modal = "rodoviario" if custo_final == C_ik_rod[i,
                                                                        k] else "aereo"

                        # Dados para o CSV de Detalhes do Modelo
                        rotas_list.append({
                            "Modelo": 2, "Usa_Penalidade": penalidade,
                            "ID_Fluxo": f"{i}_{k}_{v}", "Rota": f"{i} -> {k}", "Tipo_Rota": "ik",
                            "Origem": i, "Lat_O": municipios_coordenadas[i][0], "Lon_O": municipios_coordenadas[i][1],
                            "Destino": k, "Lat_D": estados_coordenadas[k][0], "Lon_D": estados_coordenadas[k][1],
                            "Custo_Horas": f"{custo_final:.3f}",
                            "Doses": doses,
                            "Vacina": v,
                            "Modal": modal,
                        })

                        # Acumuladores para Comparativo entre Modelos
                        total_doses += doses
                        soma_custo_ponderado += (doses * custo_final)
                        if modal == "rodoviario":
                            modal_rodoviario += 1
                        else:
                            modal_aereo += 1

                        print(
                            f"De {i} para {k} em {custo_final:.3f} hora(s) | {v}: {doses} doses (Modal: {modal})")

        print("\n[ETAPA 2] Centro Estadual K -> Município J")
        for k in K:
            for j in J:
                for v in V:
                    if y_kjv[k, j, v].X > 0:
                        doses = int(y_kjv[k, j, v].X)
                        custo_final = min(C_kj_rod[k, j], C_kj_aereo[k, j])
                        modal = "rodoviario" if custo_final == C_kj_rod[k,
                                                                        j] else "aereo"

                        # Dados para o CSV de Detalhes do Modelo
                        rotas_list.append({
                            "Modelo": 2, "Usa_Penalidade": penalidade,
                            "ID_Fluxo": f"{k}_{j}_{v}", "Rota": f"{k} -> {j}", "Tipo_Rota": "kj",
                            "Origem": k, "Lat_O": estados_coordenadas[k][0], "Lon_O": estados_coordenadas[k][1],
                            "Destino": j, "Lat_D": municipios_coordenadas[j][0], "Lon_D": municipios_coordenadas[j][1],
                            "Custo_Horas": f"{custo_final:.3f}",
                            "Doses": doses,
                            "Vacina": v,
                            "Modal": modal,
                        })

                        # Acumuladores para Comparativo entre Modelos
                        total_doses += doses
                        soma_custo_ponderado += (doses * custo_final)
                        if modal == "rodoviario":
                            modal_rodoviario += 1
                        else:
                            modal_aereo += 1

                        print(
                            f"De {k} para {j} em {custo_final:.3f} hora(s) | {v}: {doses} doses (Modal: {modal})")

        print("\n" + "-"*60)
        print(" MÉTRICAS GLOBAIS DE DESEMPENHO")
        print("-"*60)

        custo_medio_horas_por_dose = soma_custo_ponderado / \
            total_doses if total_doses > 0 else 0
        percentual_rodoviario = (modal_rodoviario / (modal_rodoviario + modal_aereo)
                                 ) * 100 if (modal_rodoviario + modal_aereo) > 0 else 0
        percenteual_aereo = (modal_aereo / (modal_rodoviario + modal_aereo)) * \
            100 if (modal_rodoviario + modal_aereo) > 0 else 0

        # 3. Dados do CSV para Registro do Comparativo
        resumo = {
            "Modelo": 2,
            "Usa_Penalidade": penalidade,
            "Custo_Medio_Horas_Por_Dose": f"{custo_medio_horas_por_dose:.3f}",
            "Valor_Funcao_Objetivo": f"{m.ObjVal:.3f}",
            "Total_Doses_Movimentadas": total_doses,
            "Total_Doses_Faltantes": resultado['doses_faltantes'],
            "Percentual_Rodoviario": f"{percentual_rodoviario:.3f}",
            "Percentual_Aereo": f"{percenteual_aereo:.3f}",
            "Tempo_Maximo": f"{resultado['L']:.3f}",
            "Vacina_Maior_Tempo": resultado['vacina'],
            "Rota_Gargalo_ij": "N/A",
            "Tempo_Rota_Gargalo_ij": "N/A",
            "Doses_Rota_Gargalo_ij": "N/A",
            "Rota_Gargalo_ik": resultado['rota_l1'],
            "Tempo_Rota_Gargalo_ik": f"{resultado['L1']:.3f}",
            "Doses_Rota_Gargalo_ik": resultado['doses_l1'],
            "Rota_Gargalo_kj": resultado['rota_l2'],
            "Tempo_Rota_Gargalo_kj": f"{resultado['L2']:.3f}",
            "Doses_Rota_Gargalo_kj": resultado['doses_l2'],
            "Rota_Gargalo_jp": "N/A",
            "Tempo_Rota_Gargalo_jp": "N/A",
            "Doses_Rota_Gargalo_jp": "N/A",
        }

        # Dados impressos no termimal
        print(f"\nTotal de doses movimentadas: {total_doses}")
        print(f"\nTotal de doses que não atenderam a demanda: {resultado['doses_faltantes']}")
        print(f"\nLimite Superior: {resultado['L']:.3f} horas")
        print(f"\nVacina do Limite Superior: {resultado['vacina']}")
        print(
            f"\nGargalo Etapa ik - {resultado['L1']:.3f} horas (Rota: {resultado['rota_l1']} ({resultado['doses_l1']} doses))")
        print(
            f"Gargalo Etapa kj - {resultado['L2']:.3f} horas (Rota: {resultado['rota_l2']} ({resultado['doses_l2']} doses))")

        print("-"*60)

        # Exportação dos Detalhes do Modelo 2
        exportar_detalhes(2, penalidade, rotas_list)
        registrar_comparativo(resumo)
        print("Processamento do Modelo 2 concluído com sucesso.")

        print("="*60 + "\n")

    else:
        print("Modelo inviavel ou sem solçao otima.")


def executar_modelo_intramunicipal(penalidade, rho_jv):

    resultado = modelo_intramunicipal(penalidade, rho_jv)

    if m.status == GRB.OPTIMAL:

        rotas_list = []
        total_doses = 0
        soma_custo_ponderado = 0.0
        custo_medio_horas_por_dose = 0.0
        modal_aereo = 0
        modal_rodoviario = 0
        percenteual_aereo = 0.0
        percentual_rodoviario = 0.0

        print("\n" + "="*60)
        print(" RELATÓRIO DE DISTRIBUIÇÃO")
        print("="*60)
        print()

        for j in J:
            for p in P[j]:
                for v in V:
                    if z[j, p, v].X > 0:
                        doses = int(z[j, p, v].X)
                        custo_final = C_jp[j, p]
                        modal = "rodoviario"

                        # Dados para o CSV de Detalhes do Modelo
                        rotas_list.append({
                            "Modelo": 3, "Usa_Penalidade": penalidade,
                            "ID_Fluxo": f"{j}_{p}_{v}", "Rota": f"{j} -> {p}", "Tipo_Rota": "jp",
                            "Origem": j, "Lat_O": municipios_coordenadas[j][0], "Lon_O": municipios_coordenadas[j][1],
                            "Destino": p, "Lat_D": postos_coordenadas[p][0], "Lon_D": postos_coordenadas[p][1],
                            "Custo_Horas": f"{custo_final:.3f}",
                            "Doses": doses,
                            "Vacina": v,
                            "Modal": modal,
                        })

                        # Acumuladores para Comparativo entre Modelos
                        total_doses += doses
                        soma_custo_ponderado += (doses * custo_final)
                        if modal == "rodoviario":
                            modal_rodoviario += 1
                        else:
                            modal_aereo += 1

                        print(
                            f"De {j} para {p} em {custo_final:.3f} hora(s) | {v}: {doses} doses (Modal: {modal})")

        print("\n" + "-"*60)
        print(" MÉTRICAS GLOBAIS DE DESEMPENHO")
        print("-"*60)

        custo_medio_horas_por_dose = soma_custo_ponderado / \
            total_doses if total_doses > 0 else 0
        percentual_rodoviario = (modal_rodoviario / (modal_rodoviario + modal_aereo)
                                 ) * 100 if (modal_rodoviario + modal_aereo) > 0 else 0
        percenteual_aereo = (modal_aereo / (modal_rodoviario + modal_aereo)) * \
            100 if (modal_rodoviario + modal_aereo) > 0 else 0

        # 3. Dados do CSV para Registro do Comparativo
        resumo = {
            "Modelo": 3,
            "Usa_Penalidade": penalidade,
            "Custo_Medio_Horas_Por_Dose": f"{custo_medio_horas_por_dose:.3f}",
            "Valor_Funcao_Objetivo": f"{m.ObjVal:.3f}",
            "Total_Doses_Movimentadas": total_doses,
            "Total_Doses_Faltantes": resultado['doses_faltantes'],
            "Percentual_Rodoviario": f"{percentual_rodoviario:.3f}",
            "Percentual_Aereo": f"{percenteual_aereo:.3f}",
            "Tempo_Maximo": f"{resultado['T']:.3f}",
            "Vacina_Maior_Tempo": resultado['vacina'],
            "Rota_Gargalo_ij": "N/A",
            "Tempo_Rota_Gargalo_ij": "N/A",
            "Doses_Rota_Gargalo_ij": "N/A",
            "Rota_Gargalo_ik": "N/A",
            "Tempo_Rota_Gargalo_ik": "N/A",
            "Doses_Rota_Gargalo_ik": "N/A",
            "Rota_Gargalo_kj": "N/A",
            "Tempo_Rota_Gargalo_kj": "N/A",
            "Doses_Rota_Gargalo_kj": "N/A",
            "Rota_Gargalo_jp": resultado['rota'],
            "Tempo_Rota_Gargalo_jp": f"{resultado['T']:.3f}",
            "Doses_Rota_Gargalo_jp": resultado['doses']
        }

        # Dados impressos no termimal
        print(f"\nTotal de doses movimentadas: {total_doses}")
        print(f"\nTotal de doses que não atenderam a demanda: {resultado['doses_faltantes']}")
        print(f"\nTempo Total de Distribuicao: {resultado['T']:.3f} horas")
        print(f"\nVacina do maior tempo: {resultado['vacina']}")
        print(
            f"\nGargalo Etapa jp - {resultado['T']:.3f} horas (Rota: {resultado['rota']} ({resultado['doses']} doses))")

        print("-"*60)

        # Exportação dos Detalhes do Modelo 3
        exportar_detalhes(3, penalidade, rotas_list)
        registrar_comparativo(resumo)
        print("Processamento do Modelo 3 concluído com sucesso.")

        print("="*60 + "\n")

    else:
        print("Modelo inviavel ou sem solçao otima.")


def executar_modelo_unificado_sem_intermediacao_estadual(penalidade, rho_iv, e_jv):
    # No unificado, considera-se que a disponibilidade intramunicipal
    # vem da etapa intermunicipal do proprio plano integrado.
    # s = {(j, v): 0 for j in J for v in V}

    # Atualiza e_jv para o estagio pos-intermunicipal e exporta para CSV.
    e_jv = calcular_e_jv_sem_intermediacao_estadual()
    rho_jv = {
        (j, v): (1 / e_jv[j, v]) if e_jv.get((j, v), 0) > 0 else 0
        for j in J for v in V
    }

    resultado = modelo_unificado_sem_intermediacao_estadual(
        penalidade, rho_iv, rho_jv)

    if m.status == GRB.OPTIMAL:

        rotas_list = []
        total_doses = 0
        soma_custo_ponderado = 0.0
        custo_medio_horas_por_dose = 0.0
        modal_aereo = 0
        modal_rodoviario = 0
        percenteual_aereo = 0.0
        percentual_rodoviario = 0.0

        # Atualiza s para o estagio pos-intermunicipal e exporta para CSV.
        s = calcular_s_sem_intermediacao_estadual()
        exportar_oferta_s_csv(s)
        exportar_expiracao_jv_csv(e_jv)

        print("\n" + "="*60)
        print(" RELATÓRIO DE DISTRIBUIÇÃO")
        print("="*60)
        print()

        print("\n[ETAPA 1] Município I -> Município J")
        for i in I:
            for j in J:
                if i != j:
                    for v in V:
                        if x_ijv[i, j, v].X > 0:
                            doses = int(x_ijv[i, j, v].X)
                            custo_final = min(C_ij_rod[i, j], C_ij_aereo[i, j])
                            modal = "rodoviario" if custo_final == C_ij_rod[i,
                                                                            j] else "aereo"

                            # Dados para o CSV de Detalhes do Modelo
                            rotas_list.append({
                                "Modelo": 4, "Usa_Penalidade": penalidade,
                                "ID_Fluxo": f"{i}_{j}_{v}", "Rota": f"{i} -> {j}", "Tipo_Rota": "ij",
                                "Origem": i, "Lat_O": municipios_coordenadas[i][0], "Lon_O": municipios_coordenadas[i][1],
                                "Destino": j, "Lat_D": municipios_coordenadas[j][0], "Lon_D": municipios_coordenadas[j][1],
                                "Custo_Horas": f"{custo_final:.3f}",
                                "Doses": doses,
                                "Vacina": v,
                                "Modal": modal,
                            })

                            # Acumuladores para Comparativo entre Modelos
                            total_doses += doses
                            soma_custo_ponderado += (doses * custo_final)
                            if modal == "rodoviario":
                                modal_rodoviario += 1
                            else:
                                modal_aereo += 1

                            print(
                                f"De {i} para {j} em {custo_final:.3f} hora(s) | {v}: {doses} doses (Modal: {modal})")

        print("\n[ETAPA 2] Município J -> Posto P")
        for j in J:
            for p in P[j]:
                for v in V:
                    if z[j, p, v].X > 0:
                        doses = int(z[j, p, v].X)
                        custo_final = C_jp[j, p]
                        modal = "rodoviario"

                        # Dados para o CSV de Detalhes do Modelo
                        rotas_list.append({
                            "Modelo": 4, "Usa_Penalidade": penalidade,
                            "ID_Fluxo": f"{j}_{p}_{v}", "Rota": f"{j} -> {p}", "Tipo_Rota": "jp",
                            "Origem": j, "Lat_O": municipios_coordenadas[j][0], "Lon_O": municipios_coordenadas[j][1],
                            "Destino": p, "Lat_D": postos_coordenadas[p][0], "Lon_D": postos_coordenadas[p][1],
                            "Custo_Horas": f"{custo_final:.3f}",
                            "Doses": doses,
                            "Vacina": v,
                            "Modal": modal,
                        })

                        # Acumuladores para Comparativo entre Modelos
                        total_doses += doses
                        soma_custo_ponderado += (doses * custo_final)
                        if modal == "rodoviario":
                            modal_rodoviario += 1
                        else:
                            modal_aereo += 1

                        print(
                            f"De {j} para {p} em {custo_final:.3f} hora(s) | {v}: {doses} doses (Modal: {modal})")

        print("\n" + "-"*60)
        print(" MÉTRICAS GLOBAIS DE DESEMPENHO")
        print("-"*60)

        custo_medio_horas_por_dose = soma_custo_ponderado / \
            total_doses if total_doses > 0 else 0
        percentual_rodoviario = (modal_rodoviario / (modal_rodoviario + modal_aereo)
                                 ) * 100 if (modal_rodoviario + modal_aereo) > 0 else 0
        percenteual_aereo = (modal_aereo / (modal_rodoviario + modal_aereo)) * \
            100 if (modal_rodoviario + modal_aereo) > 0 else 0

        # 3. Dados do CSV para Registro do Comparativo
        resumo = {
            "Modelo": 4,
            "Usa_Penalidade": penalidade,
            "Custo_Medio_Horas_Por_Dose": f"{custo_medio_horas_por_dose:.3f}",
            "Valor_Funcao_Objetivo": f"{m.ObjVal:.3f}",
            "Total_Doses_Movimentadas": total_doses,
            "Total_Doses_Faltantes": resultado['doses_faltantes'],
            "Percentual_Rodoviario": f"{percentual_rodoviario:.3f}",
            "Percentual_Aereo": f"{percenteual_aereo:.3f}",
            "Tempo_Maximo": f"{resultado['L']:.3f}",
            "Vacina_Maior_Tempo": resultado['vacina'],
            "Rota_Gargalo_ij": resultado['rota_l1'],
            "Tempo_Rota_Gargalo_ij": f"{resultado['L1']:.3f}",
            "Doses_Rota_Gargalo_ij": resultado['doses_l1'],
            "Rota_Gargalo_ik": "N/A",
            "Tempo_Rota_Gargalo_ik": "N/A",
            "Doses_Rota_Gargalo_ik": "N/A",
            "Rota_Gargalo_kj": "N/A",
            "Tempo_Rota_Gargalo_kj": "N/A",
            "Doses_Rota_Gargalo_kj": "N/A",
            "Rota_Gargalo_jp": resultado['rota_l2'],
            "Tempo_Rota_Gargalo_jp": f"{resultado['L2']:.3f}",
            "Doses_Rota_Gargalo_jp": resultado['doses_l2']
        }

        # Dados Impressos no terminal
        print(f"\nTotal de doses movimentadas: {total_doses}")
        print(f"\nTotal de doses que não atenderam a demanda: {resultado['doses_faltantes']}")
        print(f"\nLimite Superior: {resultado['L']:.3f} horas")
        print(f"\nVacina do Limite Superior: {resultado['vacina']}")
        print(
            f"\nGargalo Etapa ij - {resultado['L1']:.3f} horas (Rota: {resultado['rota_l1']} ({resultado['doses_l1']} doses))")
        print(
            f"Gargalo Etapa jp - {resultado['L2']:.3f} horas (Rota: {resultado['rota_l2']} ({resultado['doses_l2']} doses))")

        print("-"*60)

        # Exportação dos Detalhes do Modelo 4
        exportar_detalhes(4, penalidade, rotas_list)
        registrar_comparativo(resumo)
        print("Processamento do Modelo 4 concluído com sucesso.")

        print("="*60 + "\n")

    else:
        print("Modelo inviavel ou sem solçao otima.")


def executar_modelo_unificado_com_intermediacao_estadual(penalidade, rho_iv, e_jv):

    # No unificado com intermediacao, a disponibilidade intramunicipal
    # vem da etapa via centro estadual do proprio plano integrado.
    # s = {(j, v): 0 for j in J for v in V}

    # Atualiza e_jv para o estagio pos-intermunicipal e exporta para CSV.
    e_jv = calcular_e_jv_com_intermediacao_estadual()
    rho_jv = {
        (j, v): (1 / e_jv[j, v]) if e_jv.get((j, v), 0) > 0 else 0
        for j in J for v in V
    }

    resultado = modelo_unificado_com_intermediacao_estadual(
        penalidade, rho_iv, rho_jv)

    if m.status == GRB.OPTIMAL:

        rotas_list = []
        total_doses = 0
        soma_custo_ponderado = 0.0
        custo_medio_horas_por_dose = 0.0
        modal_rodoviario = 0
        modal_aereo = 0
        percenteual_aereo = 0.0
        percentual_rodoviario = 0.0

        # Atualiza s para o estagio pos-intermunicipal e exporta para CSV.
        s = calcular_s_com_intermediacao_estadual()
        exportar_oferta_s_csv(s)
        exportar_expiracao_jv_csv(e_jv)

        print("\n" + "="*60)
        print(" RELATÓRIO DE DISTRIBUIÇÃO")
        print("="*60)
        print()

        print("\n[ETAPA 1] Município I -> Centro Estadual K")
        for i in I:
            for k in K:
                for v in V:
                    if x_ikv[i, k, v].X > 0:
                        doses = int(x_ikv[i, k, v].X)
                        custo_final = min(C_ik_rod[i, k], C_ik_aereo[i, k])
                        modal = "rodoviario" if custo_final == C_ik_rod[i,
                                                                        k] else "aereo"

                        # Dados para o CSV de Detalhes do Modelo
                        rotas_list.append({
                            "Modelo": 5, "Usa_Penalidade": penalidade,
                            "ID_Fluxo": f"{i}_{k}_{v}", "Rota": f"{i} -> {k}", "Tipo_Rota": "ik",
                            "Origem": i, "Lat_O": municipios_coordenadas[i][0], "Lon_O": municipios_coordenadas[i][1],
                            "Destino": k, "Lat_D": estados_coordenadas[k][0], "Lon_D": estados_coordenadas[k][1],
                            "Custo_Horas": f"{custo_final:.3f}",
                            "Doses": doses,
                            "Vacina": v,
                            "Modal": modal,
                        })

                        # Acumuladores para Comparativo entre Modelos
                        total_doses += doses
                        soma_custo_ponderado += (doses * custo_final)
                        if modal == "rodoviario":
                            modal_rodoviario += 1
                        else:
                            modal_aereo += 1

                        print(
                            f"De {i} para {k} em {custo_final:.3f} hora(s) | {v}: {doses} doses (Modal: {modal})")

        print("\n[ETAPA 2] Centro Estadual K -> Municipio J")
        for k in K:
            for j in J:
                for v in V:
                    if y_kjv[k, j, v].X > 0:
                        doses = int(y_kjv[k, j, v].X)
                        custo_final = min(C_kj_rod[k, j], C_kj_aereo[k, j])
                        modal = "rodoviario" if custo_final == C_kj_rod[k,
                                                                        j] else "aereo"

                        # Dados para o CSV de Detalhes do Modelo
                        rotas_list.append({
                            "Modelo": 5, "Usa_Penalidade": penalidade,
                            "ID_Fluxo": f"{k}_{j}_{v}", "Rota": f"{k} -> {j}", "Tipo_Rota": "kj",
                            "Origem": k, "Lat_O": estados_coordenadas[k][0], "Lon_O": estados_coordenadas[k][1],
                            "Destino": j, "Lat_D": municipios_coordenadas[j][0], "Lon_D": municipios_coordenadas[j][1],
                            "Custo_Horas": f"{custo_final:.3f}",
                            "Doses": doses,
                            "Vacina": v,
                            "Modal": modal,
                        })

                        # Acumuladores para Comparativo entre Modelos
                        total_doses += doses
                        soma_custo_ponderado += (doses * custo_final)
                        if modal == "rodoviario":
                            modal_rodoviario += 1
                        else:
                            modal_aereo += 1

                        print(
                            f"De {k} para {j} em {custo_final:.3f} hora(s) | {v}: {doses} doses (Modal: {modal})")

        print("\n[ETAPA 3] Municipio J -> Posto P")
        for j in J:
            for p in P[j]:
                for v in V:
                    if z[j, p, v].X > 0:
                        doses = int(z[j, p, v].X)
                        custo_final = C_jp[j, p]
                        modal = "rodoviario"

                        # Dados para o CSV de Detalhes do Modelo
                        rotas_list.append({
                            "Modelo": 5, "Usa_Penalidade": penalidade,
                            "ID_Fluxo": f"{j}_{p}_{v}", "Rota": f"{j} -> {p}", "Tipo_Rota": "jp",
                            "Origem": j, "Lat_O": municipios_coordenadas[j][0], "Lon_O": municipios_coordenadas[j][1],
                            "Destino": p, "Lat_D": postos_coordenadas[p][0], "Lon_D": postos_coordenadas[p][1],
                            "Custo_Horas": f"{custo_final:.3f}",
                            "Doses": doses,
                            "Vacina": v,
                            "Modal": modal,
                        })

                        # Acumuladores para Comparativo entre Modelos
                        total_doses += doses
                        soma_custo_ponderado += (doses * custo_final)
                        if modal == "rodoviario":
                            modal_rodoviario += 1
                        else:
                            modal_aereo += 1

                        print(
                            f"De {j} para {p} em {custo_final:.3f} hora(s) | {v}: {doses} doses (Modal: {modal})")

        print("\n" + "-"*60)
        print(" MÉTRICAS GLOBAIS DE DESEMPENHO")
        print("-"*60)

        custo_medio_horas_por_dose = soma_custo_ponderado / \
            total_doses if total_doses > 0 else 0
        percentual_rodoviario = (modal_rodoviario / (modal_rodoviario + modal_aereo)
                                 ) * 100 if (modal_rodoviario + modal_aereo) > 0 else 0
        percenteual_aereo = (modal_aereo / (modal_rodoviario + modal_aereo)) * \
            100 if (modal_rodoviario + modal_aereo) > 0 else 0

        # 3. Dados do CSV para Registro do Comparativo
        resumo = {
            "Modelo": 5,
            "Usa_Penalidade": penalidade,
            "Custo_Medio_Horas_Por_Dose": f"{custo_medio_horas_por_dose:.3f}",
            "Valor_Funcao_Objetivo": f"{m.ObjVal:.3f}",
            "Total_Doses_Movimentadas": total_doses,
            "Total_Doses_Faltantes": resultado['doses_faltantes'],
            "Percentual_Rodoviario": f"{percentual_rodoviario:.3f}",
            "Percentual_Aereo": f"{percenteual_aereo:.3f}",
            "Tempo_Maximo": f"{resultado['L']:.3f}",
            "Vacina_Maior_Tempo": resultado['vacina'],
            "Rota_Gargalo_ij": "N/A",
            "Tempo_Rota_Gargalo_ij": "N/A",
            "Doses_Rota_Gargalo_ij": "N/A",
            "Rota_Gargalo_ik": resultado['rota_l1'],
            "Tempo_Rota_Gargalo_ik": f"{resultado['L1']:.3f}",
            "Doses_Rota_Gargalo_ik": resultado['doses_l1'],
            "Rota_Gargalo_kj": resultado['rota_l2'],
            "Tempo_Rota_Gargalo_kj": f"{resultado['L2']:.3f}",
            "Doses_Rota_Gargalo_kj": resultado['doses_l2'],
            "Rota_Gargalo_jp": resultado['rota_l3'],
            "Tempo_Rota_Gargalo_jp": f"{resultado['L3']:.3f}",
            "Doses_Rota_Gargalo_jp": resultado['doses_l3']
        }

        # Dados Impressos no Terminal
        print(f"\nTotal de doses movimentadas: {total_doses}")
        print(f"\nTotal de doses que não atenderam a demanda: {resultado['doses_faltantes']}")
        print(f"\nLimite Superior: {resultado['L']:.3f} horas")
        print(f"\nVacina do Limite Superior: {resultado['vacina']}")
        print(
            f"\nGargalo Etapa ik - {resultado['L1']:.3f} horas (Rota: {resultado['rota_l1']} ({resultado['doses_l1']} doses))")
        print(
            f"Gargalo Etapa kj - {resultado['L2']:.3f} horas (Rota: {resultado['rota_l2']} ({resultado['doses_l2']} doses))")
        print(
            f"Gargalo Etapa jp - {resultado['L3']:.3f} horas (Rota: {resultado['rota_l3']} ({resultado['doses_l3']} doses))")

        print("-"*60)

        # Exportação dos Detalhes do Modelo 5
        exportar_detalhes(5, penalidade, rotas_list)
        registrar_comparativo(resumo)
        print("Processamento do Modelo 5 concluído com sucesso.")

        print("="*60 + "\n")

    else:
        print("Modelo inviavel ou sem solçao otima.")


match modelo:
    case 1:
        executar_modelomodelo_intermunicipal_sem_intermediacao_estadual(
            penalidade, rho_iv)

    case 2:
        executar_modelomodelo_intermunicipal_com_intermediacao_estadual(
            penalidade, rho_iv)

    case 3:
        executar_modelo_intramunicipal(penalidade, rho_jv)

    case 4:
        executar_modelo_unificado_sem_intermediacao_estadual(
            penalidade, rho_iv, e_jv)

    case 5:
        executar_modelo_unificado_com_intermediacao_estadual(
            penalidade, rho_iv, e_jv)

    case _:
        print("Opçao invalida. Encerrando o programa.")
