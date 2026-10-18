"""
T4 — T3 + madeixas frontais fora da cara.

Resultado visual da T3 (captura do utilizador + diagnostic_report.json):
  - CONFIRMADO: o morph deslocava o centro do olho -6,07 cm em Z (bake_effect).
    Na T3 os olhos ja ficam a altura certa (0.933 da altura do corpo, separacao 5.96 cm).
  - Na imagem nao se ve olho nenhum: as madeixas frontais da B7 (azimute +/-45 graus em
    torno da frente) nascem na linha do cabelo e caem a y ~ -0.13 m, ou seja, em cima
    da superficie da cara, e a x = 0, +/-0.02, +/-0.04 m — exactamente sobre os olhos
    (x = +/-0.030 m).  (INFERIDO por calculo a partir das medidas do relatorio.)

CORRECAO DA T4 (unica diferenca face a T3):
  as 7 madeixas frontais passam a nascer nas laterais da cabeca (azimute 85-103 graus
  para cada lado da frente), em vez de a frente. O resto e exactamente o create_female_t3.py.

Implementacao: le o codigo da T3, aplica o patch de texto abaixo (falha se o trecho nao
for encontrado) e renomeia T3 -> T4 nos objectos, materiais e ficheiros de saida.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent
T3_PATH = HERE / "create_female_t3.py"
src = T3_PATH.read_text(encoding="utf-8")

OLD_STRAND = """        if is_frontal:
            phi_birth = math.pi * 0.30
            strand_len_factor = 0.75
            swing_back = 0.45
"""
NEW_STRAND = """        if is_frontal:
            # T4: madeixas frontais passam para as laterais (nao cobrem a cara/olhos)
            side_sign = -1.0 if i < 18 else 1.0
            azimuth = FRONT_PHI + side_sign * math.radians(85.0 + 6.0 * abs(i - 18))
            phi_birth = math.pi * 0.35
            strand_len_factor = 0.75
            swing_back = 0.45
"""
if src.count(OLD_STRAND) != 1:
    raise RuntimeError("T4: trecho das madeixas frontais nao encontrado na T3 (ficheiro mudou?)")
src = src.replace(OLD_STRAND, NEW_STRAND)

OLD_LIM = "Cabelo em curvas NURBS; madeixas frontais ainda caem a frente do pescoco/peito (herdado da B7)"
NEW_LIM = ("Cabelo em curvas NURBS; madeixas frontais movidas para as laterais na T4 "
           "(podem atravessar os ombros)")
src = src.replace(OLD_LIM, NEW_LIM)

# T3 -> T4 em nomes de objectos, materiais, ficheiros e versao
src = src.replace("T3", "T4").replace("t3", "t4")

exec(compile(src, str(HERE / "create_female_t4_generated.py"), "exec"),
     {"__name__": "__main__", "__file__": str(T3_PATH)})
