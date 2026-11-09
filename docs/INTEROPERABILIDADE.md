# Verificação independente e interoperabilidade

O runtime de Zonas GeoJSON usa somente a biblioteca padrão. Shapely, NumPy e
Fiona são dependências exclusivas de QA, fixadas em `requirements-interop.txt`.
Nenhum dos dois verificadores importa `zonas_geojson` ou chama sua API interna.
O programa é exercitado pelo executável instalado, em outro diretório, com
entradas sintéticas e perfil temporários.

## Três verificações diferentes

`tools/check_bundle.py` usa `zipfile`, `hashlib`, `json`, `csv` e ElementTree da
biblioteca padrão. Confere os oito nomes e sua ordem, timestamps e permissões,
CRC, tamanhos e SHA-256 do manifesto. Reconstrói resumos e contagens por zona a
partir das correspondências, verifica status, ordem Unicode, coordenadas,
orçamento lógico, linhas CSV e a neutralização de fórmulas em IDs. Confere a
preservação de propriedades, geometria e ordem no GeoJSON anotado, incluindo o
JSON escalar `zg_matches`. No SVG, verifica XML, ausência de recursos externos,
todos os marcadores/IDs/status, no máximo quarenta rótulos de pontos e os anéis
de cada componente com `fill-rule="evenodd"`. Isso verifica a estrutura gráfica;
não certifica a aparência ou a precisão física de um mapa.

Com `--demo`, o leitor também exige os treze pontos, as quatro geometrias,
todas as relações e o resumo escritos manualmente na especificação do exemplo.
O ouro não é criado pela biblioteca sob teste. São quinze linhas de dados CSV,
incluindo uma por correspondência e uma para cada ponto externo.

`tools/verify_external.py` produz três ZIPs novos pela CLI instalada:

| Caso | Oráculo | Alcance |
| --- | --- | --- |
| Exemplo | Ouro literal + Shapely/GEOS | 13 pontos × 4 zonas, todos os 52 pares |
| Gerado | Shapely/GEOS | 723 pontos × 5 zonas, todos os 3.615 pares |
| Precisão | Ouro decimal literal | `1 − 10⁻¹⁸`, `1`, `1 + 10⁻¹⁸`: interior, borda, externo |

O caso gerado contém exterior com furo, zonas sobrepostas, triângulo, polígono
côncavo e MultiPolygon separado. Inclui vértices, pontos médios e uma grade
pseudoaleatória com semente fixa. Coordenadas inteiras, meios e quartos são
representáveis exatamente em binário. O verificador confirma essa propriedade
com `Fraction` antes de convertê-las para Shapely. `contains` distingue o
interior; `covers` inclui a borda, inclusive a de um furo. Todos os pares são
conferidos, incluindo os externos; nenhuma zona vencedora é escolhida.

O caso de dezoito casas **não usa um oráculo de ponto flutuante**. Seu JSON é
literal e o leitor usa `Decimal`. A igualdade das coordenadas originais com as
saídas analítica, anotada e CSV é verificada sem conversão para `float`. GEOS e
GDAL não participam desse ouro: arredondar esses três números para `double`
apagaria justamente a diferença que está sendo testada.

## Consumidor GIS real

Para o exemplo e o caso gerado, Fiona abre o arquivo `pontos-anotados.geojson`
extraído do ZIP recém-produzido, usando o driver **GDAL GeoJSON**. Escreve uma
camada Point em **GeoPackage** e a reabre com o driver GDAL GPKG. Nas três
leituras são conferidos todos os IDs autorais em `zg_point_id`, status,
contagens, indicador de borda, conteúdo de `zg_matches`, coordenadas e
propriedades originais escalares. O caso gerado exercita nomes em português,
aspas, `&`, `<`, IDs com líderes de fórmula e ordem de fonte diferente da
ordem analítica.

No fluxo JSON, a comparação recursiva distingue booleanos de números:
`true` não pode virar `1`, inclusive dentro de listas e objetos. A única
conversão semântica aceita explicitamente no consumidor GIS é um campo booleano
ser armazenado como inteiro `0`/`1` pelo driver/SQLite. Os demais tipos escalares
e os textos JSON de correspondências são conferidos sem essa equivalência.

Uma segunda leitura usa somente `sqlite3`: executa `PRAGMA integrity_check`,
confere `gpkg_contents`, `gpkg_geometry_columns`, campos e linhas. As
coordenadas são decodificadas diretamente do header GeoPackage e WKB Point2D
com `struct`, sem carregar extensões espaciais, Fiona ou Shapely nessa leitura.
O ID de feição numérico de um driver não substitui o ID autoral preservado em
`zg_point_id`.

Esses ensaios comprovam o fluxo local testado, com geometrias e campos usados
nos casos. Não certificam todos os consumidores, todas as propriedades JSON,
todo o GeoJSON/OGC ou qualquer interpretação cadastral. O motor analisa
segmentos cartesianos em graus; não calcula geodésia, áreas ou distâncias.

## Reproduzir

Em Linux, com `uv` disponível e o wheel construído:

```sh
uv venv --python 3.13 .venv-interop
uv pip install --python .venv-interop/bin/python --only-binary :all: \
  --index-url https://pypi.org/simple -r requirements-interop.txt
uv venv --python 3.13 /tmp/zonas-cli-qa
uv pip install --python /tmp/zonas-cli-qa/bin/python --no-deps \
  dist/zonas_geojson-1.0.0-py3-none-any.whl
.venv-interop/bin/python tools/verify_external.py \
  --cli /tmp/zonas-cli-qa/bin/zonas-geojson --report /tmp/interop-nova.json
python3 tools/check_bundle.py dist/exemplo-zonas.zip --demo
python3 -m unittest discover -s tests -p test_independent.py -v
```

`--workdir` aceita um diretório pai existente para temporários. O relatório
JSON omite caminhos de host, credenciais e dados pessoais; registra versões,
hashes, contagens e conclusões da execução. Um relatório versionado é uma
evidência histórica; reproduzir exige executar os comandos e obter outro
relatório. Falhas retornam código 2 sem emitir aprovação.

`--keep-demo DIRETORIO` retém apenas os dois artefatos sintéticos do exemplo
de treze pontos após todas as verificações passarem. A execução registrada
inclui [`demo-verificado.zip`](../fixtures/interop/demo-verificado.zip) e
[`demo-consumidor.gpkg`](../fixtures/interop/demo-consumidor.gpkg). Seus tamanhos
e hashes constam em `retainedArtifacts` no relatório. O ZIP pode ser conferido
com `check_bundle.py --demo`; o GPKG pode ser aberto por outro consumidor GIS
ou inspecionado diretamente com SQLite. O ZIP é determinístico; o GeoPackage
pode incluir timestamps do driver, portanto seu hash identifica esse snapshot.

O subprocesso da CLI recebe um ambiente mínimo, sem `PYTHONPATH`, perfil e
cache temporários e proxies locais indisponíveis. O script não consulta rede.
Esse isolamento de ambiente não equivale a bloquear sockets no kernel. Para
provar operação sem rede, a execução adicional deve usar o isolamento de rede
do sistema/CI depois de baixar o wheel e as dependências. Não são necessários
`sudo`, atualização global ou acesso a arquivos pessoais.

Quando namespaces de usuário e rede estiverem habilitados no Linux:

```sh
unshare -Urn -- .venv-interop/bin/python tools/verify_external.py \
  --cli /tmp/zonas-cli-qa/bin/zonas-geojson --require-network-isolation \
  --report /tmp/interop-sem-rede.json
```

Essa opção exige que o kernel exponha somente a interface loopback e nenhuma
rota IPv4. A execução registrada em
[`fixtures/interop/execucao-2026-11-09.json`](../fixtures/interop/execucao-2026-11-09.json)
foi realizada dessa forma e passou: 3.667 pares GEOS, 736 pontos no ciclo
GeoJSON/GPKG/SQLite e três casos decimais com ouro manual. O ambiente QA carregou
Python 3.13.1, Shapely 2.2.0/GEOS 3.14.1 e Fiona 1.10.1/GDAL 3.9.2; a CLI
instalada foi executada com Python 3.14.4. O registro contém SHA-256 dos nove
arquivos de runtime instalados, permitindo compará-los ao wheel final mesmo
que metadados de empacotamento/documentação mudem seu hash global.
O lançamento final usou também ambiente limpo e perfil/diretório de trabalho
temporários para o próprio processo QA, antes de importar Fiona e Shapely;
essas condições estão registradas em `qaLaunch`.

## Fontes primárias e versões

Metadados oficiais consultados em 9 de novembro de 2026:
[Shapely no PyPI](https://pypi.org/pypi/shapely/json),
[Fiona no PyPI](https://pypi.org/pypi/fiona/json). Foram fixados Shapely 2.2.0
e Fiona 1.10.1, além das dependências transitivas. Fiona 1.10.1 publica wheels
para Python 3.13 e não para 3.14 nessa consulta, razão do Python 3.13 no QA GIS.
As versões efetivamente carregadas de GEOS e GDAL são registradas pelo script.

Definições dos predicados:
[Shapely `contains`](https://shapely.readthedocs.io/en/stable/reference/shapely.contains.html),
[Shapely `covers`](https://shapely.readthedocs.io/en/stable/reference/shapely.covers.html).
Fluxo de leitura/escrita e schema:
[manual oficial Fiona](https://fiona.readthedocs.io/en/stable/manual.html).
Modelos dos formatos e drivers:
[GDAL GeoJSON](https://gdal.org/en/stable/drivers/vector/geojson.html),
[GDAL GeoPackage](https://gdal.org/en/stable/drivers/vector/gpkg.html).
O recorte de entrada e suas restrições adicionais estão documentados na API;
o padrão de referência é o [RFC 7946](https://www.rfc-editor.org/rfc/rfc7946).
