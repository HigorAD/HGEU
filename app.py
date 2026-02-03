# app.py — Horários (sem cache; arquivos locais; perfis público/admin por URL)
# Python 3.8+ compatível

import os
import re
import csv
import unicodedata
import importlib.util
from io import BytesIO
from typing import List, Optional, Tuple, Dict, Any

import streamlit as st
import pandas as pd

# ====== ReportLab (para exportar PDF) ======
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet

# ====== ReportLab (para exportar PDF) ======
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet

# ---- Compatibilidade Windows/Python 3.8: hashlib.md5(usedforsecurity=...) ----
try:
    from reportlab.pdfbase import pdfdoc as _pdfdoc
    _orig_md5 = _pdfdoc.md5  # função importada internamente do hashlib
    def _md5_compat(*args, **kwargs):
        # Alguns builds de Python/OpenSSL não aceitam o kw 'usedforsecurity'
        kwargs.pop("usedforsecurity", None)
        return _orig_md5(*args, **kwargs)
    _pdfdoc.md5 = _md5_compat
except Exception:
    # Se algo mudar em versões futuras, seguimos sem patch (não atrapalha)
    pass

# =========================
# Import robusto de convert.py (usa process_df)
# =========================
HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from convert import process_df  # type: ignore
except ModuleNotFoundError:
    convert_path = os.path.join(HERE, "convert.py")
    spec = importlib.util.spec_from_file_location("convert", convert_path)
    convert = importlib.util.module_from_spec(spec)  # type: ignore
    assert spec and spec.loader
    spec.loader.exec_module(convert)  # type: ignore
    process_df = convert.process_df  # type: ignore
# (o process_df normaliza e explode a coluna Turma em Turma_list e gera o fact "agenda")  # [1](https://unipead-my.sharepoint.com/personal/higor_delsoto_docente_unip_br/Documents/Microsoft%20Copilot%20Chat%20Files/convert.py)


# =========================
# Arquivos locais (na mesma pasta do app)
# =========================
DATA_CSV = os.path.join(HERE, "TabelaGeralDisicplinas_2026_1.csv")
DATA_XLSX = os.path.join(HERE, "TabelaGeralDisicplinas_2026_1.xlsx")
DISPONIBILIDADE_SOURCE = os.path.join(HERE, "disponibilidade_professores.csv")

def find_data_source() -> str:
    """Escolhe o arquivo de horário existente: CSV ou XLSX (nessa ordem)."""
    if os.path.exists(DATA_CSV):
        return DATA_CSV
    if os.path.exists(DATA_XLSX):
        return DATA_XLSX
    return ""


# =========================
# Config Streamlit + CSS leve
# =========================
st.set_page_config(page_title="Horários — Professores e Alunos", layout="wide")
st.markdown("", unsafe_allow_html=True)
st.title("Horários — Professores e Alunos")

# =========================
# Perfis simples por URL (?role=admin habilita abas gerenciais)
# =========================
def get_query_params() -> Dict[str, str]:
    try:
        # Streamlit >= 1.30
        return dict(st.query_params)
    except Exception:
        # Compatibilidade
        return {
            k: (v[0] if isinstance(v, list) and v else v)
            for k, v in st.experimental_get_query_params().items()  # type: ignore[attr-defined]
        }

qp = get_query_params()
role = (qp.get("role") or "public").strip().lower()
is_admin = (role == "admin")


# =========================
# Constantes de exibição
# =========================
COLS_TURNOS = ["Pré", "1º", "2º"]
DIAS_ORD = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta"]
DIAS_DISP = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado"]

# =========================
# Período (Manhã/Noite) conforme regra: A,B = Manhã | P,Q,R,S = Noite
# =========================
PERIODO_MAP = {"A": "Manhã", "B": "Manhã", "P": "Noite", "Q": "Noite", "R": "Noite", "S": "Noite"}

def infer_periodo(turma_id: str) -> str:
    """ Inferir período a partir do código (captura A/B/P/Q/R/S entre números, p.ex. CC1P12 -> P). """
    if not isinstance(turma_id, str):
        return "Indefinido"
    t = turma_id.strip().upper()
    m = re.search(r"(?<=\d)([ABPQRS])(?=\d)", t)
    if not m:
        return "Indefinido"
    return PERIODO_MAP.get(m.group(1), "Indefinido")


# =========================
# Estilo por tipo (T, P, EAD)
# =========================
TYPE_STYLE = {
    "T": "background-color: #E8F1FF; color: #0B3D91; font-weight: 600;",
    "P": "background-color: #E9FBEA; color: #0F5D1A; font-weight: 600;",
    "EAD": "background-color: #FFF3E0; color: #8A4B00; font-weight: 600;",
}

def style_cell_by_tipo(val: Any) -> str:
    """
    Se a célula contém [EAD], [P] ou [T], aplica cor.
    Prioridade: EAD > P > T (em célula mista).
    """
    if not isinstance(val, str) or not val.strip():
        return ""
    v = val.upper()
    if "[EAD]" in v:
        return TYPE_STYLE["EAD"]
    if "[P]" in v:
        return TYPE_STYLE["P"]
    if "[T]" in v:
        return TYPE_STYLE["T"]
    return ""


# =========================
# Helpers de comparação/normalização de nomes
# =========================
def norm_text(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = s.strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s

def best_match_nome(nome_dispon: str, nomes_horario: List[str]) -> Optional[str]:
    """
    Casa nome completo (disponibilidade) com nome do horário (abreviado).
    Estratégia: se algum nome do horário for substring do nome completo (ou vice-versa), pega o mais longo.
    """
    nd = norm_text(nome_dispon)
    cand: List[Tuple[int, str]] = []
    for nh in nomes_horario:
        nnh = norm_text(nh)
        if nnh and (nnh in nd):
            cand.append((len(nnh), nh))
        elif nd and (nd in nnh):
            cand.append((len(nd), nh))
    if not cand:
        return None
    cand.sort(reverse=True)
    return cand[0][1]


# =========================
# Leitura do arquivo de horário e conversão (sem cache)
# =========================
def load_and_convert_horario(path: str) -> Dict[str, pd.DataFrame]:
    if not path or not os.path.exists(path):
        raise FileNotFoundError(
            "Arquivo de horário não encontrado. Coloque ao lado do app:\n"
            "- TabelaGeralDisicplinas_2026_1.csv ou\n"
            "- TabelaGeralDisicplinas_2026_1.xlsx"
        )
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        df_raw = pd.read_csv(path, dtype=str, keep_default_na=False, comment="#", skip_blank_lines=True)
    else:
        df_raw = pd.read_excel(path, dtype=str, engine="openpyxl")
    dfs, _report = process_df(df_raw)
    return dfs
# (o DF consolidado no app vem de agenda + dimensões, conforme app.py original)  # [2](https://unipead-my.sharepoint.com/personal/higor_delsoto_docente_unip_br/Documents/Microsoft%20Copilot%20Chat%20Files/app.py)


# =========================
# Leitura de disponibilidade (2 formatos)
# =========================
def load_disponibilidade(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    # Lê linhas cruas para detectar delimitador e blocos
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw_lines = [ln.rstrip("\n") for ln in f.readlines()]
    # remove comentários; preserva vazias para separar blocos
    lines: List[str] = []
    for ln in raw_lines:
        if ln.lstrip().startswith("#"):
            continue
        lines.append(ln)
    # Caso (1): header com Periodo presente
    joined_non_empty = "\n".join([ln for ln in lines if ln.strip()])
    if joined_non_empty:
        first_line = joined_non_empty.splitlines()[0]
        if re.search(r"(,|;|\t)\s*Periodo\s*(,|;|\t|$)", first_line, flags=re.I):
            sep = "\t" if "\t" in first_line else (";" if ";" in first_line else ",")
            df = pd.read_csv(path, dtype=str, keep_default_na=False, sep=sep, comment="#", skip_blank_lines=True)
            df.columns = [c.strip() for c in df.columns]
            for c in ["Periodo", "Funcional", "Nome", "NomeHorario"]:
                if c in df.columns:
                    df[c] = df[c].astype(str).str.strip()
            for d in DIAS_DISP:
                if d not in df.columns:
                    df[d] = ""
                df[d] = df[d].astype(str).str.strip().str.lower()
            if "Funcional" in df.columns:
                df["Funcional"] = df["Funcional"].astype(str).str.strip()
            if "Nome" in df.columns:
                df["Nome"] = df["Nome"].astype(str).str.strip()
            df = df[~((df.get("Funcional", "") == "") & (df.get("Nome", "") == ""))].copy()
            return df
    # Caso (2): dois blocos com cabeçalho repetido (1º=Noite, 2º=Manhã)
    header_line = None
    for ln in lines:
        if ln.strip() and ("Funcional" in ln and "Nome" in ln):
            header_line = ln
            break
    if header_line is None:
        return pd.DataFrame()
    sep = "\t" if "\t" in header_line else (";" if ";" in header_line else ",")
    rows: List[Dict[str, str]] = []
    current_header: Optional[List[str]] = None
    block_idx = -1  # 0=noite, 1=manhã
    for ln in lines:
        if not ln.strip():
            continue
        parts = next(csv.reader([ln], delimiter=sep))
        parts = [p.strip() for p in parts]
        if parts and parts[0].lower() == "funcional":
            current_header = parts
            block_idx += 1
            continue
        if current_header is None:
            continue
        if len(parts) < len(current_header):
            parts = parts + [""] * (len(current_header) - len(parts))
        rec = dict(zip(current_header, parts))
        periodo = "Noite" if block_idx == 0 else ("Manhã" if block_idx == 1 else "Indefinido")
        rec["Periodo"] = periodo
        for d in DIAS_DISP:
            rec.setdefault(d, "")
        rows.append(rec)
    df = pd.DataFrame(rows)
    df.columns = [c.strip() for c in df.columns]
    for d in DIAS_DISP:
        df[d] = df[d].astype(str).str.strip().str.lower()
    if "NomeHorario" not in df.columns:
        df["NomeHorario"] = ""
    if "Funcional" in df.columns:
        df["Funcional"] = df["Funcional"].astype(str).str.strip()
    if "Nome" in df.columns:
        df["Nome"] = df["Nome"].astype(str).str.strip()
    df = df[~((df.get("Funcional", "") == "") & (df.get("Nome", "") == ""))].copy()
    return df


# =========================
# Helper: exibir grade com estilo por tipo
# =========================
def show_grid(df_grid: pd.DataFrame):
    grid = df_grid.copy()
    if grid.index.name is not None:
        grid = grid.reset_index().rename(columns={"dia_semana": "Dia"})
    try:
        styler = grid.style.applymap(style_cell_by_tipo, subset=[c for c in COLS_TURNOS if c in grid.columns])
        col_cfg = {
            "Dia": st.column_config.TextColumn("Dia", width="small"),
            "Pré": st.column_config.TextColumn("Pré", width="medium"),
            "1º": st.column_config.TextColumn("1º", width="medium"),
            "2º": st.column_config.TextColumn("2º", width="medium"),
        }
        for k in list(col_cfg.keys()):
            if k not in grid.columns:
                col_cfg.pop(k, None)
        st.dataframe(styler, use_container_width=True, column_config=col_cfg)
    except Exception:
        st.dataframe(grid, use_container_width=True)


# =========================
# Carregar dados do horário e montar DF enriquecido
# =========================
try:
    data_path = find_data_source()
    dfs = load_and_convert_horario(data_path)
except Exception as e:
    st.error(str(e))
    st.stop()

cursos = dfs["cursos"]
turmas = dfs["turmas"]
disciplinas = dfs["disciplinas"]
professores = dfs["professores"]
agenda = dfs["agenda"]

DF = (
    agenda.merge(disciplinas, on="disc_id", how="left")
          .merge(professores, on="prof_id", how="left")
          .merge(turmas, on="turma_id", how="left")
          .merge(cursos, on="curso_id", how="left")
)
# Se o convert.py já escreveu 'junc_src' no fact, manteremos (o app sabe usar isso na aba de Junções)  # [1](https://unipead-my.sharepoint.com/personal/higor_delsoto_docente_unip_br/Documents/Microsoft%20Copilot%20Chat%20Files/convert.py)
DF["periodo"] = DF["turma_id"].apply(infer_periodo)  # [2](https://unipead-my.sharepoint.com/personal/higor_delsoto_docente_unip_br/Documents/Microsoft%20Copilot%20Chat%20Files/app.py)


# =========================
# TABS (público/admin)
# =========================
tabs_public = [
    "Horário por Turma",
    "Agenda do Professor",
    "Horários (Todas as Turmas)",
    "Junções (Grids Separados)",  # NOVA ABA pública
]
tabs_labels = tabs_public + (["Disponibilidade (Professores)", "Conflitos"] if is_admin else [])
tabs = st.tabs(tabs_labels)

if is_admin:
    st.info("🔒 Modo Coordenação (admin). Adicionais: Disponibilidade e Conflitos.")

# Mapeia tabs para variáveis
if is_admin:
    tab_turma, tab_prof, tab_all, tab_junc_grids, tab_disp, tab_conf = tabs  # type: ignore[misc]
else:
    tab_turma, tab_prof, tab_all, tab_junc_grids = tabs  # type: ignore[misc]


# =========================
# Aba 1 — Horário por Turma (com agrupamento de mesma disciplina)
# =========================
with tab_turma:
    turma_sel = st.selectbox(
        "Selecione a turma",
        sorted(DF["turma_id"].dropna().unique().tolist()),
        key="turma_sel_one",
    )
    df_turma = DF[DF["turma_id"] == turma_sel].copy()

    # Agrupa por dia/turno + disciplina + tipo e une professores
    grouped = (
        df_turma.groupby(["dia_semana", "turno", "nome_disciplina", "tipo"], dropna=False)
                .agg(profs=("nome_professor", lambda s: sorted(set([p for p in s if isinstance(p, str) and p.strip()]))))
                .reset_index()
    )

    def fmt_cell(row: pd.Series) -> str:
        disc = row.get("nome_disciplina") or ""
        tipo = row.get("tipo") or ""
        profs = row.get("profs") or []
        profs_txt = " / ".join(profs) if profs else ""
        return f"{disc} ({profs_txt}) [{tipo}]".strip()

    grouped["cell"] = grouped.apply(fmt_cell, axis=1)
    pivot = (
        grouped.pivot_table(
            index="dia_semana",
            columns="turno",
            values="cell",
            aggfunc=lambda x: "\n".join([v for v in x if isinstance(v, str) and v.strip()]),
        )
        .reindex(DIAS_ORD)
        .reindex(columns=COLS_TURNOS)
    )
    st.subheader(f"Horário — Turma {turma_sel}")
    show_grid(pivot)
    st.caption('Cada linha: "Disciplina (Professor(es)) [Tipo]". Tipos: T=Teórica, P=Prática, EAD=Assíncrona.')  # [2](https://unipead-my.sharepoint.com/personal/higor_delsoto_docente_unip_br/Documents/Microsoft%20Copilot%20Chat%20Files/app.py)


# =========================
# Aba 2 — Agenda do Professor (separada por Manhã/Noite/Indefinido)
# =========================
with tab_prof:
    prof_sel = st.selectbox(
        "Selecione o professor",
        sorted(DF["nome_professor"].dropna().unique().tolist()),
        key="prof_sel_one",
    )
    df_prof = DF[DF["nome_professor"] == prof_sel].copy()

    def montar_grade_prof(df_base: pd.DataFrame) -> pd.DataFrame:
        grouped = (
            df_base.groupby(["dia_semana", "turno", "nome_disciplina", "tipo"], dropna=False)
                   .agg(turmas=("turma_id", lambda s: sorted(set([x for x in s if isinstance(x, str) and x.strip()]))))
                   .reset_index()
        )

        def fmt_cell(row: pd.Series) -> str:
            disc = row["nome_disciplina"] or ""
            tipo = row["tipo"] or ""
            turmas_txt = " / ".join(row["turmas"]) if row["turmas"] else ""
            return f"{disc} [{tipo}] ({turmas_txt})".strip()

        grouped["cell"] = grouped.apply(fmt_cell, axis=1)
        pivot_prof = (
            grouped.pivot_table(
                index="dia_semana",
                columns="turno",
                values="cell",
                aggfunc=lambda x: "\n".join([v for v in x if isinstance(v, str) and v.strip()]),
            )
            .reindex(DIAS_ORD)
            .reindex(columns=COLS_TURNOS)
        )
        return pivot_prof

    st.subheader(f"Agenda — {prof_sel}")
    df_manha = df_prof[df_prof["periodo"] == "Manhã"]
    df_noite = df_prof[df_prof["periodo"] == "Noite"]
    df_indef = df_prof[df_prof["periodo"] == "Indefinido"]

    if not df_manha.empty:
        st.markdown("### ☀️ Manhã")
        show_grid(montar_grade_prof(df_manha))
    if not df_noite.empty:
        st.markdown("### 🌙 Noite")
        show_grid(montar_grade_prof(df_noite))
    if not df_indef.empty:
        st.markdown("### ❓ Indefinido")
        show_grid(montar_grade_prof(df_indef))


# =========================
# Aba 3 (pública) — Horários (Todas as Turmas)
# =========================
with tab_all:
    st.subheader("Horários — Todas as Turmas")

    curso_opts = ["(Todos)"] + sorted(DF["curso_nome"].dropna().unique().tolist())
    curso_sel = st.selectbox("Curso", curso_opts, index=0, key="curso_all")

    periodo_opts = ["(Todos)", "Manhã", "Noite", "Indefinido"]
    periodo_sel_all = st.selectbox("Período", periodo_opts, index=0, key="periodo_all")

    ocultar_vazias = st.checkbox("Ocultar turmas sem aulas no filtro atual", value=True, key="ocultar_all")

    base = DF if curso_sel == "(Todos)" else DF[DF["curso_nome"] == curso_sel].copy()
    turmas_list = sorted(base["turma_id"].dropna().unique().tolist())

    def pivot_turma(df_turma: pd.DataFrame) -> pd.DataFrame:
        grouped = (
            df_turma.groupby(["dia_semana", "turno", "nome_disciplina", "tipo"], dropna=False)
                    .agg(profs=("nome_professor", lambda s: sorted(set([p for p in s if isinstance(p, str) and p.strip()]))))
                    .reset_index()
        )

        def fmt_cell(row: pd.Series) -> str:
            disc = row.get("nome_disciplina") or ""
            tipo = row.get("tipo") or ""
            profs = row.get("profs") or []
            profs_txt = " / ".join(profs) if profs else ""
            return f"{disc} ({profs_txt}) [{tipo}]".strip()

        grouped["cell"] = grouped.apply(fmt_cell, axis=1)
        pivot = (
            grouped.pivot_table(
                index="dia_semana",
                columns="turno",
                values="cell",
                aggfunc=lambda x: "\n".join([v for v in x if isinstance(v, str) and v.strip()]),
            )
            .reindex(DIAS_ORD)
            .reindex(columns=COLS_TURNOS)
        )
        return pivot

    if not turmas_list:
        st.info("Nenhuma turma encontrada para o filtro selecionado.")
    else:
        for turma_id in turmas_list:
            df_t = DF[DF["turma_id"] == turma_id].copy()
            if periodo_sel_all != "(Todos)":
                df_t = df_t[df_t["periodo"] == periodo_sel_all]
            if df_t.empty and ocultar_vazias:
                continue
            pivot = pivot_turma(df_t)
            # Se todas as células estiverem vazias e 'ocultar' estiver marcado, pula
            if ocultar_vazias:
                is_all_empty = True
                if pivot is not None and not pivot.empty:
                    is_all_empty = pivot.fillna("").applymap(lambda x: str(x).strip() == "").all().all()
                if is_all_empty:
                    continue
            st.markdown(f"### Turma {turma_id}")
            show_grid(pivot)
            st.divider()


# =========================
# Aba 4 — Junções (Grids Separados) — pública
# =========================
# =========================
# Aba 4 — Junções (Grids Separados) — pública
# =========================
with tab_junc_grids:
    st.subheader("Junções de Turmas — Grids Separados")

    # Filtros
    curso_opts = ["(Todos)"] + sorted(DF["curso_nome"].dropna().unique().tolist())
    curso_sel = st.selectbox("Curso", curso_opts, index=0, key="curso_junc_grids")

    periodo_opts = ["(Todos)", "Manhã", "Noite", "Indefinido"]
    periodo_sel = st.selectbox("Período", periodo_opts, index=0, key="periodo_junc_grids")

    somente_praticas = st.checkbox("Mostrar apenas aulas práticas (P)", value=False, key="praticas_junc_grids")

    # -------------------------
    # Base filtrada
    # -------------------------
    base = DF.copy()
    if curso_sel != "(Todos)":
        base = base[base["curso_nome"] == curso_sel]
    if periodo_sel != "(Todos)":
        base = base[base["periodo"] == periodo_sel]

    # NORMALIZAÇÃO DO TIPO
    base["tipo"] = base["tipo"].astype(str).str.strip().str.upper().replace({"": None, "NAN": None})

    # FILTRO DE TIPOS:
    # - quando checkbox DESMARCADO  -> {T, P, EAD}
    # - quando checkbox MARCADO     -> {P}
    tipos_alvo = {"P"} if somente_praticas else {"T", "P", "EAD"}
    base = base[base["tipo"].isin(tipos_alvo)]

    if base.empty:
        st.info("Nenhum dado encontrado para os filtros selecionados.")
        st.stop()

    # Coletaremos os pivots para exportação em PDF ao final
    pivots_para_pdf = []

    # Formatter de células no padrão do app
    def fmt_cell_junc(row: pd.Series) -> str:
        disc = row.get("nome_disciplina") or ""
        tipo = (row.get("tipo") or "").strip()  # T, P, EAD
        prof = row.get("nome_professor") or ""  # pode estar vazio para EAD
        tipo_tag = f"[{tipo}]" if tipo else ""
        return f"{disc} ({prof}) {tipo_tag}".strip()

    # -------------------------
    # Caminho 1: usar junc_src (se existir) — garante TODAS as disciplinas da junção original
    # -------------------------
    if "junc_src" in base.columns and base["junc_src"].astype(str).str.strip().any():
        # Lista de junções válidas (2+ turmas)
        grupos = (
            base.loc[base["junc_src"].astype(str).str.strip() != "", ["junc_src"]]
                .assign(junc_size=lambda df: df["junc_src"].str.split(r"\s*/\s*")
                        .apply(lambda xs: len([t for t in xs if t])))
                .query("junc_size >= 2")
                .drop_duplicates()
                .sort_values(["junc_size", "junc_src"], ascending=[False, True])
                .reset_index(drop=True)
        )

        def get_turmas_from_jkey(jkey: str) -> List[str]:
            return [t for t in re.split(r"\s*/\s*", jkey) if t.strip()]

        for _, g in grupos.iterrows():
            key = g["junc_src"]
            turmas_do_grupo = get_turmas_from_jkey(key)
            n_t = g["junc_size"]

            # Pega TODAS as linhas dessa junção (já filtradas por curso/período/tipos)
            df_g = base[base["junc_src"] == key].copy()

            # Agrupa por aula (disciplina+professor+tipo) por slot — evita "misturar" professores
            grouped = (
                df_g.groupby(["dia_semana", "turno", "nome_disciplina", "tipo", "nome_professor"], dropna=False)
                     .size().reset_index(name="n")
            )
            grouped["cell"] = grouped.apply(fmt_cell_junc, axis=1)

            pivot = (
                grouped.pivot_table(
                    index="dia_semana",
                    columns="turno",
                    values="cell",
                    aggfunc=lambda x: "\n".join([v for v in x if isinstance(v, str) and v.strip()]),
                )
                .reindex(DIAS_ORD)
                .reindex(columns=COLS_TURNOS)
            )

            st.markdown(f"### Junção: {key}  \n<sub>{n_t} turmas</sub>", unsafe_allow_html=True)
            show_grid(pivot)
            st.divider()

            pivots_para_pdf.append((key, pivot))

    # -------------------------
    # Caminho 2: fallback sem junc_src — reconstrói junções por slot (inclui professor na chave)
    # -------------------------
    else:
        st.warning("Convert não forneceu 'junc_src'. Exibindo junções por slot (fallback).")
        slot = (
            base.groupby(["dia_semana", "turno", "nome_disciplina", "tipo", "nome_professor"], dropna=False)
                .agg(turmas=("turma_id", lambda s: sorted(set([x for x in s if isinstance(x, str) and x.strip()]))))
                .reset_index()
        )
        # Apenas slots que têm 2+ turmas (junção real)
        slot = slot[slot["turmas"].apply(lambda xs: isinstance(xs, list) and len(xs) >= 2)].copy()

        if slot.empty:
            st.success("Não há junções de turmas nos filtros atuais.")
            st.stop()

        slot["junc_src"] = slot["turmas"].apply(lambda xs: " / ".join(xs))
        slot["junc_size"] = slot["turmas"].apply(lambda xs: len(xs))

        grupos = (
            slot.groupby("junc_src", as_index=False)
                .agg(junc_size=("junc_size", "max"), turmas=("turmas", "first"))
                .sort_values(["junc_size", "junc_src"], ascending=[False, True])
                .reset_index(drop=True)
        )

        for _, g in grupos.iterrows():
            key = g["junc_src"]
            n_t = g["junc_size"]

            df_g = slot[slot["junc_src"] == key].copy()
            df_g["cell"] = df_g.apply(fmt_cell_junc, axis=1)

            pivot = (
                df_g.pivot_table(
                    index="dia_semana",
                    columns="turno",
                    values="cell",
                    aggfunc=lambda x: "\n".join([v for v in x if isinstance(v, str) and v.strip()]),
                )
                .reindex(DIAS_ORD)
                .reindex(columns=COLS_TURNOS)
            )

            st.markdown(f"### Junção: {key}  \n<sub>{n_t} turmas</sub>", unsafe_allow_html=True)
            show_grid(pivot)
            st.divider()

            pivots_para_pdf.append((key, pivot))    

    # ===== Exportar PDF (uma página por junção) =====
    def gerar_pdf_juncoes(pivots: List[Tuple[str, pd.DataFrame]], titulo_doc: str = "Junções de Turmas") -> bytes:
        """
        pivots: lista de tuplas (junc_key, pivot_df)
        Retorna bytes do PDF.
        """
        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=landscape(A4),
            leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18
        )
        styles = getSampleStyleSheet()
        flow = []

        for idx, (jkey, pv) in enumerate(pivots):
            flow.append(Paragraph(f"{titulo_doc} — {jkey}", styles["Title"]))
            flow.append(Spacer(1, 8))

            # Converte o DataFrame pivot em tabela (cabeçalho + linhas) na mesma ordem do app
            cols = [c for c in COLS_TURNOS if c in (pv.columns if isinstance(pv.columns, pd.Index) else [])]
            data = [["Dia"] + cols]

            # Garantir linhas para todos os dias na mesma ordem visual
            for dia in DIAS_ORD:
                row_vals = []
                if (pv is not None) and (not pv.empty) and (dia in pv.index):
                    for c in cols:
                        val = pv.loc[dia, c] if c in pv.columns else ""
                        row_vals.append("" if pd.isna(val) else str(val))
                else:
                    row_vals = ["" for _ in cols]
                data.append([dia] + row_vals)

            tbl = Table(data, repeatRows=1)
            tbl.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0F0F0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#333333")),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("ALIGN", (0, 1), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BDBDBD")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FBFBFB")]),
            ]))
            flow.append(tbl)
            if idx < len(pivots) - 1:
                flow.append(PageBreak())

        doc.build(flow)
        pdf = buf.getvalue()
        buf.close()
        return pdf

    if pivots_para_pdf:
        pdf_bytes = gerar_pdf_juncoes(pivots_para_pdf, titulo_doc="Junções de Turmas")
        st.download_button(
            "⬇️ Exportar PDF (todas as junções listadas)",
            data=pdf_bytes,
            file_name="juncoes_de_turmas.pdf",
            mime="application/pdf",
            key="btn_pdf_juncoes"
        )


# =========================
# Aba 5 — Disponibilidade (Professores) — somente admin
# =========================
if is_admin:
    with tab_disp:
        st.subheader("Disponibilidade x Atribuição (por Professor)")
        disp_df = load_disponibilidade(DISPONIBILIDADE_SOURCE)
        if disp_df.empty:
            st.warning(
                "Arquivo `disponibilidade_professores.csv` não encontrado ou vazio na pasta do app.\n"
                "Coloque-o ao lado do `app.py` para habilitar esta aba."
            )
        else:
            # Dias disponíveis por linha (x)
            def dias_disp(row: pd.Series) -> List[str]:
                out: List[str] = []
                for d in DIAS_DISP:
                    if str(row.get(d, "")).strip().lower() == "x":
                        out.append(d)
                return out

            disp_df["dias_disponiveis"] = disp_df.apply(dias_disp, axis=1)

            # Resolver nome do horário (NomeHorario prioritário; senão casar automaticamente)
            nomes_horario = sorted(DF["nome_professor"].dropna().unique().tolist())

            def resolve_nome_horario(row: pd.Series) -> str:
                nh = str(row.get("NomeHorario", "")).strip()
                if nh:
                    return nh
                nome = str(row.get("Nome", "")).strip()
                return best_match_nome(nome, nomes_horario) or ""

            disp_df["NomeHorario_resolvido"] = disp_df.apply(resolve_nome_horario, axis=1)

            # Atribuições (por período)
            periodo_sel_disp = st.selectbox("Período", ["Manhã", "Noite"], key="periodo_disp")
            disp_p = disp_df[disp_df["Periodo"].astype(str).str.lower() == periodo_sel_disp.lower()].copy()
            df_p = DF[DF["periodo"] == periodo_sel_disp].copy()

            atribu = (
                df_p.groupby("nome_professor")["dia_semana"]
                    .apply(lambda s: sorted(set([x for x in s if isinstance(x, str) and x.strip()])))
                    .reset_index(name="dias_atribuidos")
            )

            base = disp_p.merge(
                atribu, left_on="NomeHorario_resolvido", right_on="nome_professor", how="left"
            )
            base["dias_atribuidos"] = base["dias_atribuidos"].apply(lambda x: x if isinstance(x, list) else [])
            base["disp_set"] = base["dias_disponiveis"].apply(lambda x: set(x) if isinstance(x, list) else set())
            base["atr_set"] = base["dias_atribuidos"].apply(lambda x: set(x) if isinstance(x, list) else set())

            base["Disponível"] = base["disp_set"].apply(lambda s: ", ".join(sorted(s)))
            base["Atribuído"] = base["atr_set"].apply(lambda s: ", ".join(sorted(s)))
            base["Disponível e Livre"] = (base["disp_set"] - base["atr_set"]).apply(lambda s: ", ".join(sorted(s)))
            base["Atribuído fora da disponibilidade"] = (base["atr_set"] - base["disp_set"]).apply(
                lambda s: ", ".join(sorted(s))
            )

            # Alertas de nomes não casados
            nao_casados = base[(base["NomeHorario_resolvido"] == "") & (base["Atribuído"] == "")]
            if not nao_casados.empty:
                st.info(
                    "Alguns professores não foram casados automaticamente com o nome do horário.\n"
                    "👉 Para ficar perfeito, preencha a coluna **NomeHorario** no arquivo de disponibilidade\n"
                    "com o mesmo nome que aparece no horário."
                )
                st.dataframe(nao_casados[["Funcional", "Nome", "Periodo"]], use_container_width=True)

            resumo = base[
                ["Funcional", "Nome", "NomeHorario_resolvido", "Disponível", "Atribuído",
                 "Disponível e Livre", "Atribuído fora da disponibilidade"]
            ].rename(columns={"NomeHorario_resolvido": "NomeHorario"})

            st.markdown("#### Resumo (por professor)")
            st.dataframe(resumo, use_container_width=True)

            st.markdown("#### Visão por dia (matriz)")

            def status_por_dia(row: pd.Series, dia: str) -> str:
                d_disp = dia in row["disp_set"]
                d_atr = dia in row["atr_set"]
                if d_atr and d_disp:
                    return "✅ Atribuído (OK)"
                if d_disp and not d_atr:
                    return "🟡 Livre (Disponível)"
                if d_atr and not d_disp:
                    return "🔴 Atribuído (fora disp.)"
                return "—"

            matriz = pd.DataFrame(
                {
                    "Professor": resumo["NomeHorario"].where(resumo["NomeHorario"] != "", resumo["Nome"]),
                    **{d: base.apply(lambda r: status_por_dia(r, d), axis=1) for d in DIAS_DISP},
                }
            )
            st.dataframe(matriz, use_container_width=True)


# =========================
# Aba 6 — Conflitos — somente admin
# =========================
if is_admin:
    with tab_conf:
        st.subheader("Conflitos — Disponibilidade e Choques de Horário")

        disp_df = load_disponibilidade(DISPONIBILIDADE_SOURCE)
        if disp_df.empty:
            st.warning(
                "Arquivo `disponibilidade_professores.csv` não encontrado ou vazio na pasta do app.\n"
                "A análise de 'fora da disponibilidade' ficará indisponível."
            )

        # Resolver NomeHorario_resolvido
        nomes_horario = sorted(DF["nome_professor"].dropna().unique().tolist())
        if not disp_df.empty:
            disp_df["dias_disponiveis"] = disp_df.apply(
                lambda r: [d for d in DIAS_DISP if str(r.get(d, "")).strip().lower() == "x"], axis=1
            )

            def resolve_nome_horario(row: pd.Series) -> str:
                nh = str(row.get("NomeHorario", "")).strip()
                if nh:
                    return nh
                nome = str(row.get("Nome", "")).strip()
                return best_match_nome(nome, nomes_horario) or ""

            disp_df["NomeHorario_resolvido"] = disp_df.apply(resolve_nome_horario, axis=1)

        # Dados base para conflitos (exclui professores vazios — evita ruído, ex.: EAD com '*')
        df_aulas = DF.copy()
        df_aulas = df_aulas[
            df_aulas["nome_professor"].notna() & (df_aulas["nome_professor"].astype(str).str.strip() != "")
        ]

        # --- Mapa de disponibilidade por (professor, período) -> set(dias)
        disp_map: Dict[Tuple[str, str], set] = {}
        if not disp_df.empty:
            for _, r in disp_df.iterrows():
                prof_nome = str(r.get("NomeHorario_resolvido", "")).strip()
                periodo = str(r.get("Periodo", "")).strip()
                if not prof_nome or not periodo:
                    continue
                disp_map.setdefault((prof_nome, periodo), set()).update(set(r.get("dias_disponiveis", []) or []))

        # --- Conflito 1: Fora da disponibilidade (dia)
        def fora_disp(row: pd.Series) -> bool:
            key = (row.get("nome_professor"), row.get("periodo"))
            dia = row.get("dia_semana")
            if not dia or key not in disp_map:
                return False
            return dia not in disp_map[key]

        df_fora = df_aulas[df_aulas.apply(fora_disp, axis=1)].copy()

        # --- Conflito 2: Choques por professor (duas+ disciplinas no mesmo dia/turno)
        g = (
            df_aulas.groupby(["nome_professor", "periodo", "dia_semana", "turno"], dropna=False)
                    .agg(
                        n_disc=("nome_disciplina", lambda s: len(set([x for x in s if isinstance(x, str) and x.strip()]))),
                        disciplinas=("nome_disciplina", lambda s: sorted(set([x for x in s if isinstance(x, str) and x.strip()]))),
                        turmas=("turma_id", lambda s: sorted(set([x for x in s if isinstance(x, str) and x.strip()]))),
                        tipos=("tipo", lambda s: sorted(set([str(x).strip() for x in s if str(x).strip()]))),
                    )
                    .reset_index()
        )
        df_choques_prof = g[g["n_disc"] > 1].copy()

        # --- Conflito 3: Choques por turma (ignora turno "Pré")
        df_aulas_turma = df_aulas[df_aulas["turno"].astype(str).str.strip() != "Pré"].copy()
        gt = (
            df_aulas_turma.groupby(["turma_id", "periodo", "dia_semana", "turno"], dropna=False)
                          .agg(
                              n_disc=("nome_disciplina", lambda s: len(set([x for x in s if isinstance(x, str) and x.strip()]))),
                              disciplinas=("nome_disciplina", lambda s: sorted(set([x for x in s if isinstance(x, str) and x.strip()]))),
                              professores=("nome_professor", lambda s: sorted(set([x for x in s if isinstance(x, str) and x.strip()]))),
                              tipos=("tipo", lambda s: sorted(set([str(x).strip() for x in s if str(x).strip()]))),
                          )
                          .reset_index()
        )
        df_choques_turma = gt[gt["n_disc"] > 1].copy()

        # --- Filtros
        st.markdown("### Filtros")
        professores_opts = ["(Todos)"] + sorted(df_aulas["nome_professor"].dropna().unique().tolist())
        prof_sel_conf = st.selectbox("Professor", professores_opts, index=0, key="prof_conf")

        turmas_opts = ["(Todas)"] + sorted(df_aulas["turma_id"].dropna().unique().tolist())
        turma_sel_conf = st.selectbox("Turma", turmas_opts, index=0, key="turma_conf")

        periodo_opts_conf = ["(Todos)", "Manhã", "Noite", "Indefinido"]
        periodo_sel_conf = st.selectbox("Período", periodo_opts_conf, index=0, key="periodo_conf")

        def filtra(df: pd.DataFrame) -> pd.DataFrame:
            out = df.copy()
            if prof_sel_conf != "(Todos)" and "nome_professor" in out.columns:
                out = out[out["nome_professor"] == prof_sel_conf]
            if turma_sel_conf != "(Todas)" and "turma_id" in out.columns:
                out = out[out["turma_id"] == turma_sel_conf]
            if periodo_sel_conf != "(Todos)" and "periodo" in out.columns:
                out = out[out["periodo"] == periodo_sel_conf]
            return out

        df_fora_f = filtra(df_fora)
        df_choques_prof_f = filtra(df_choques_prof)
        df_choques_turma_f = filtra(df_choques_turma)

        # --- Métricas
        c1, c2, c3 = st.columns(3)
        c1.metric("Fora da disponibilidade (dia)", int(len(df_fora_f)))
        c2.metric("Choques por professor", int(len(df_choques_prof_f)))
        c3.metric("Choques por turma (ignora 'Pré')", int(len(df_choques_turma_f)))

        # --- Tabelas
        st.markdown("#### 1) Aulas atribuídas fora da disponibilidade (por dia)")
        if df_fora_f.empty:
            st.success("Nenhuma atribuição fora da disponibilidade com os filtros atuais.")
        else:
            view_fora = df_fora_f[
                ["nome_professor", "periodo", "dia_semana", "turno", "turma_id", "nome_disciplina", "tipo"]
            ].sort_values(["nome_professor", "periodo", "dia_semana", "turno"])
            st.dataframe(view_fora, use_container_width=True)

        st.markdown("#### 2) Choques por professor (duas ou mais disciplinas no mesmo dia/turno)")
        if df_choques_prof_f.empty:
            st.success("Nenhum choque por professor com os filtros atuais.")
        else:
            v2 = df_choques_prof_f.copy()
            v2["disciplinas"] = v2["disciplinas"].apply(lambda x: " / ".join(x) if isinstance(x, list) else str(x))
            v2["turmas"] = v2["turmas"].apply(lambda x: " / ".join(x) if isinstance(x, list) else str(x))
            v2["tipos"] = v2["tipos"].apply(lambda x: " / ".join(x) if isinstance(x, list) else str(x))
            v2 = v2[
                ["nome_professor", "periodo", "dia_semana", "turno", "n_disc", "disciplinas", "turmas", "tipos"]
            ].sort_values(["nome_professor", "periodo", "dia_semana", "turno"])
            st.dataframe(v2, use_container_width=True)

        st.markdown("#### 3) Choques por turma (duas ou mais disciplinas no mesmo dia/turno) — ignora 'Pré'")
        if df_choques_turma_f.empty:
            st.success("Nenhum choque por turma com os filtros atuais.")
        else:
            v3 = df_choques_turma_f.copy()
            v3["disciplinas"] = v3["disciplinas"].apply(lambda x: " / ".join(x) if isinstance(x, list) else str(x))
            v3["professores"] = v3["professores"].apply(lambda x: " / ".join(x) if isinstance(x, list) else str(x))
            v3["tipos"] = v3["tipos"].apply(lambda x: " / ".join(x) if isinstance(x, list) else str(x))
            v3 = v3[
                ["turma_id", "periodo", "dia_semana", "turno", "n_disc", "disciplinas", "professores", "tipos"]
            ].sort_values(["turma_id", "periodo", "dia_semana", "turno"])
            st.dataframe(v3, use_container_width=True)