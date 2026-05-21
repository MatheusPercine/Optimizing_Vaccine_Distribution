import math
from pathlib import Path

import folium
import pandas as pd


BRAZIL_CENTER = (-14.2350, -51.9253)
DEFAULT_ZOOM = 4

# Map Tipo_Rota to origin/destination node types.
# Adjust if your model uses different semantics.
TIPO_ROTA_NODE_TYPES = {
    "ij": ("centro", "municipio"),
    "ik": ("centro", "estado"),
    "kj": ("estado", "municipio"),
    "jp": ("municipio", "posto"),
}

NODE_TYPE_COLORS = {
    "centro": "#f39c12",     # orange (match municipio)
    "estado": "#8e44ad",     # purple
    "municipio": "#f39c12",  # orange
    "posto": "#c0392b",      # red
    "desconhecido": "#7f8c8d",  # gray
}


def scale_weight(doses: pd.Series, min_w: float = 1.5, max_w: float = 8.0) -> pd.Series:
    """Scale line weights using log1p to emphasize large flows."""
    doses_safe = doses.fillna(0).clip(lower=0)
    if doses_safe.max() == doses_safe.min():
        return pd.Series([min_w] * len(doses_safe), index=doses_safe.index)

    log_vals = doses_safe.apply(lambda x: math.log1p(x))
    norm = (log_vals - log_vals.min()) / (log_vals.max() - log_vals.min())
    return min_w + norm * (max_w - min_w)


def build_node_index(df: pd.DataFrame) -> dict:
    """Create a node index with inferred node types and coordinates."""
    nodes = {}
    for _, row in df.iterrows():
        tipo_rota = str(row.get("Tipo_Rota", "")).strip()
        origin_type, dest_type = TIPO_ROTA_NODE_TYPES.get(
            tipo_rota, ("desconhecido", "desconhecido")
        )

        origin_key = row.get("Origem")
        dest_key = row.get("Destino")

        if origin_key not in nodes:
            nodes[origin_key] = {
                "lat": row.get("Lat_O"),
                "lon": row.get("Lon_O"),
                "type": origin_type,
            }
        if dest_key not in nodes:
            nodes[dest_key] = {
                "lat": row.get("Lat_D"),
                "lon": row.get("Lon_D"),
                "type": dest_type,
            }

    return nodes


def add_nodes_to_map(m: folium.Map, nodes: dict) -> None:
    """Add node markers to the map."""
    for name, info in nodes.items():
        if pd.isna(info.get("lat")) or pd.isna(info.get("lon")):
            continue
        node_type = info.get("type", "desconhecido")
        color = NODE_TYPE_COLORS.get(
            node_type, NODE_TYPE_COLORS["desconhecido"])
        folium.CircleMarker(
            location=(info["lat"], info["lon"]),
            radius=4,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            tooltip=f"{name} ({node_type})",
        ).add_to(m)


def get_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return azimuth bearing in degrees (0-360) from origin to destination."""
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    d_lon = lon2_rad - lon1_rad
    x = math.cos(lat2_rad) * math.sin(d_lon)
    y = math.cos(lat1_rad) * math.sin(lat2_rad) - \
        math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(d_lon)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def add_routes_to_map(m: folium.Map, df: pd.DataFrame) -> None:
    """Add route arcs as polylines with style based on modal and doses."""
    weights = scale_weight(df["Doses"])

    for idx, row in df.iterrows():
        lat_o = row.get("Lat_O")
        lon_o = row.get("Lon_O")
        lat_d = row.get("Lat_D")
        lon_d = row.get("Lon_D")

        if pd.isna(lat_o) or pd.isna(lon_o) or pd.isna(lat_d) or pd.isna(lon_d):
            continue

        modal = str(row.get("Modal", "")).strip().lower()
        is_aereo = modal == "aereo"

        color = "#0b2e59" if is_aereo else "#1e7f1e"
        dash_array = "5, 5" if is_aereo else None

        tooltip = (
            f"Rota: {row.get('Rota')}<br>"
            f"Tipo de Rota: {row.get('Tipo_Rota')}<br>"
            f"Doses: {row.get('Doses')}<br>"
            f"Custo_Horas: {row.get('Custo_Horas')}<br>"
            f"Vacina: {row.get('Vacina')}"
        )

        line_opacity = 0.8
        folium.PolyLine(
            locations=[(lat_o, lon_o), (lat_d, lon_d)],
            color=color,
            weight=weights.loc[idx],
            opacity=line_opacity,
            dash_array=dash_array,
            tooltip=tooltip,
        ).add_to(m)

        fator = 0.85
        lat_seta = lat_o + (lat_d - lat_o) * fator
        lon_seta = lon_o + (lon_d - lon_o) * fator

        bearing = get_bearing(lat_o, lon_o, lat_d, lon_d)
        arrow_rotation = bearing - 90
        folium.RegularPolygonMarker(
            location=(lat_seta, lon_seta),
            number_of_sides=3,
            radius=6,
            rotation=arrow_rotation,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=line_opacity,
        ).add_to(m)


def add_legend(m: folium.Map) -> None:
    """Add a fixed HTML legend overlay to the map."""
    legend_html = """
    <div style="
        position: fixed;
        bottom: 30px;
        left: 30px;
        z-index: 9999;
        background: #edf4f5;
        padding: 12px 14px;
        border-radius: 10px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.15);
        font-family: Arial, sans-serif;
        font-size: 13px;
        color: #2c3e50;
        line-height: 1.4;
        min-width: 170px;
    ">
        <div style="font-weight: 700; margin-bottom: 6px;">Legenda</div>
        <div style="font-weight: 700; margin: 6px 0 4px;">Arestas</div>
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
            <span style="display: inline-block; width: 28px; height: 0; border-top: 3px dashed #0b2e59;"></span>
            <span>Modal Aereo</span>
        </div>
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px;">
            <span style="display: inline-block; width: 28px; height: 0; border-top: 3px solid #1e7f1e;"></span>
            <span>Modal Rodoviario</span>
        </div>
        <div style="font-weight: 700; margin: 6px 0 4px;">Nós</div>
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
            <span style="display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #f39c12;"></span>
            <span>Municipio</span>
        </div>
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
            <span style="display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #8e44ad;"></span>
            <span>Centro Estadual</span>
        </div>
        <div style="display: flex; align-items: center; gap: 8px;">
            <span style="display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #c0392b;"></span>
            <span>Posto de Vacinacao</span>
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))


def build_map(df: pd.DataFrame, output_path: Path) -> None:
    """Create a Folium map for the given dataframe and save it."""
    m = folium.Map(location=BRAZIL_CENTER,
                   zoom_start=DEFAULT_ZOOM, tiles="OpenStreetMap")
    add_routes_to_map(m, df)
    nodes = build_node_index(df)
    add_nodes_to_map(m, nodes)
    add_legend(m)
    m.save(str(output_path))


def main() -> None:
    base_dir = Path(__file__).resolve().parent / "resultados_caso_nacional"
    inputs = [
        (base_dir / "detalhes_mod1_penFALSE.csv",
         "mapa_modelo1_sem_penalidade.html"),
        (base_dir / "detalhes_mod1_penTRUE.csv",
         "mapa_modelo1_com_penalidade.html"),
        (base_dir / "detalhes_mod2_penFALSE.csv",
         "mapa_modelo2_sem_penalidade.html"),
        (base_dir / "detalhes_mod2_penTRUE.csv",
         "mapa_modelo2_com_penalidade.html"),
    ]

    for csv_path, output_name in inputs:
        if not csv_path.exists():
            raise FileNotFoundError(
                f"Arquivo nao encontrado: {csv_path.resolve()}")

        df = pd.read_csv(csv_path)
        build_map(df, Path(output_name))


if __name__ == "__main__":
    main()
