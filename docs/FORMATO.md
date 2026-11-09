# Formato e relatórios

Zonas GeoJSON 1.0.0 faz uma junção local entre duas FeatureCollections. Aceita
pontos 2D e zonas Polygon/MultiPolygon. Os segmentos são cartesianos em graus
de longitude e latitude, conforme o recorte descrito aqui. Não há geocodificação,
rede, mapa-base, cálculo de área ou distância geodésica. A exatidão se refere aos
decimais fornecidos, sem afirmar precisão física ou validade cadastral.

## JSON e limites de entrada

As entradas são UTF-8 estrito sem BOM. Chaves duplicadas, Unicode com surrogate,
NaN/Infinity e sintaxe que não seja JSON são recusados. `loads(bytes | str)` usa
`int` para inteiros e `Decimal` para demais números: nunca converte para float.
`dumps(value)` produz JSON com indentação de dois espaços e LF final, mantém
ordem dos objetos e valores decimais exatos sem depender do contexto Decimal.

O codec aceita apenas objetos `dict` e arrays `list` comuns, strings, `int`,
`Decimal` finito, booleanos e null. Não executa métodos de tipos personalizados.
A API revalida mesmo documentos que já vieram de um parser. Limites por entrada:

| Recurso | Limite |
| --- | --- |
| Bytes originais e representação canônica pretty2+LF | 8388608 (8 MiB) |
| Profundidade de objetos/arrays | 16; a coleção raiz tem profundidade 1 |
| Nós JSON | 200000; cada valor, objeto ou array conta um, chaves não contam |
| Lexema numérico | 64 caracteres |
| Dígitos da mantissa, incluindo zeros escritos | 40 |
| Expoente explícito | entre -24 e 24 |

Decimais podem ser escritos em forma fixa ou científica. A representação de
saída pode escolher um expoente permitido para preservar o valor. Não há
arredondamento: `1.000000000000000001` continua diferente de `1`.
Os limites de dígitos/expoente não mudam para números presentes em propriedades.

## Esquema GeoJSON aceito

O esquema é fechado: uma FeatureCollection tem apenas `type` e `features`;
cada Feature tem `type`, `id`, `properties` e `geometry`; a geometria tem `type`
e `coordinates`. Não há bbox, crs, altitude, membros estrangeiros, geometria
null ou propriedades null. `properties` é objeto JSON arbitrário dentro dos
limites do codec e nunca interfere na geometria.

IDs são strings Unicode de 1 a 80 pontos de código, únicas em cada coleção,
sem espaços externos, C0, DEL, surrogates ou U+2028/U+2029. IDs são comparados e
ordenados pela ordem Unicode do Python. Nome ou ID colocado em propriedades
não altera a identificação da Feature. Nos pontos, as chaves `zg_point_id`,
`zg_status`, `zg_count`, `zg_boundary` e `zg_matches` são reservadas.

As coordenadas são números exatos, não booleanos nem float. Longitude fica em
[-180, 180], latitude em [-90, 90], com até 18 casas decimais após normalização
do valor. Não se normaliza -180 para 180 e não se reprojeta a entrada.

Até 10000 pontos, 100 zonas e 5000 vértices no conjunto das zonas, incluindo
fechamentos. Cada anel possui de 4 a 1000 posições e cada polígono admite até
64 furos. Coleções vazias são válidas. Anéis têm primeira e última posições
iguais, área não nula, sem repetições internas ou auto-interseções. Segmentos
colineares consecutivos podem continuar na mesma direção, sem retorno ou
sobreposição. O sentido de percurso do anel não altera o resultado.

Furos ficam estritamente dentro do exterior, sem tocar, cruzar ou conter outros
furos. Componentes de MultiPolygon ficam totalmente separados. Componentes que
se tocam não são suportados neste recorte. Zonas distintas podem sobrepor-se
ou tocar-se. Cada anel tem amplitude de longitude menor que 180 graus; não se
aceitam bordas que atravessariam o antimeridiano. Componentes já recortados
em lados separados podem coexistir.

## Resultado da junção

`analyze(points_document, zones_document, *, max_checks=2000000)` retorna um
`JoinResult` imutável. `analyze_json(points, zones, *, max_checks=2000000)` aplica
o codec estrito antes de chamar o mesmo motor. `to_dict()`, `source_points()`,
`source_zones()` e `annotated_points()` devolvem cópias novas. As entradas não
são alteradas e aliases mutáveis não são retidos.

O teste usa orientação, ponto sobre segmento e ray crossing com regra half-open
nos vértices, calculados com racionais exatos. Um ponto no interior de exterior
e fora dos furos tem relação `inside`. Sobre qualquer anel, inclusive borda de
furo, a relação é `boundary`. As relações de um MultiPolygon formam uma união.

| Status do ponto | Condição |
| --- | --- |
| outside | Nenhuma correspondência |
| inside | Uma correspondência, no interior |
| boundary | Uma correspondência, na borda |
| ambiguous | Duas ou mais correspondências, de qualquer relação |

Nenhuma zona é escolhida como vencedora. `pointsWithBoundary` conta também
pontos ambíguos que tocam alguma borda. Os quatro totais de status são disjuntos
e somam `pointCount`. `matchCount` é o total de relações, limitado a 100000.

`to_dict()` tem `version: 1`, `algorithm: "exact-ray-crossing"`, `summary`,
`points`, `zones` e `work`. Cada ponto tem `id`, `coordinates`, `status`,
`matches`; cada match tem `zoneId`, `relation`. Cada zona tem `id`, `pointCount`,
`insideCount`, `boundaryCount`. Pontos e zonas são ordenados por ID; matches,
por ID da zona. `summary` contém `pointCount`, `zoneCount`, `matchCount`,
`insideCount`, `boundaryCount`, `ambiguousCount`, `outsideCount`,
`pointsWithBoundary`.

O orçamento aceita de 1 a 20000000 checks. Um teste de interseção de segmentos
ou teste de um segmento durante localização de ponto consome um check. Inclui
validação topológica e junção. `work.checks = topologyChecks + joinChecks` e
não excede `work.limit`; `bboxRejectedPairs` conta pares ponto-zona rejeitados
pelos limites retangulares sem consumir checks. Superar o orçamento ou o limite
de matches recusa toda a operação, sem saída parcial. Estes limites não são
promessas absolutas de tempo de execução ou memória.

## Conteúdo do ZIP

Os oito nomes são fixos. A ordem ZIP é lexicográfica, o timestamp é
1980-01-01 00:00:00, a permissão é 0644 e não há caminhos do computador,
relógio atual ou dependências externas incorporados. A compressão é STORED.

| Nome | Conteúdo |
| --- | --- |
| pontos.geojson | Bytes originais dos pontos, sem reformatar |
| zonas.geojson | Bytes originais das zonas, sem reformatar |
| resultado.json | `JoinResult.to_dict()` com números exatos |
| correspondencias.csv | Uma linha por match; uma linha por ponto outside |
| pontos-anotados.geojson | Fontes preservadas por valor com cinco propriedades novas |
| mapa.svg | Figura estática cartesiana; furos evenodd |
| LEIA-ME.md | Significado, limites e instruções de leitura |
| manifesto.json | Versão 1, SHA-256 e tamanho de cada um dos outros sete arquivos |

O manifesto tem a forma `{"version":1,"files":{"nome":{"sha256":"hex","size":123}}}`.
Não contém seu próprio hash. Hashes, CRC ZIP e bytes preparados são verificados
antes da publicação. `build_report(result, points_bytes, zones_bytes) -> bytes`
exige que as fontes decodificadas correspondam às fontes imutáveis do resultado.
`write_report(path, result, points_bytes, zones_bytes) -> Path` prepara e publica
o mesmo ZIP exclusivamente, sem sobrescrita.

Documentos JSON derivados usam `dumps_output(value)`, com limites próprios de
67108864 bytes (64 MiB) e 1000000 nós; profundidade, tipos, Unicode e números
mantêm as mesmas regras. A soma dos oito arquivos do ZIP não pode superar
268435456 bytes (256 MiB), antes de empacotar. Isso permite relatórios maiores
que uma entrada de 8 MiB. Exceder esses limites também recusa a publicação por
inteiro. O codec de entrada continua recusando documentos acima de seus limites;
para ler uma saída maior, use um leitor JSON compatível com Decimal.

O GeoJSON anotado preserva a ordem original das Features, propriedades e valores
das coordenadas. Acrescenta `zg_point_id`, `zg_status`, `zg_count`,
`zg_boundary` e `zg_matches`. `zg_matches` é uma **string JSON compacta**, com
Unicode preservado, que precisa ser decodificada explicitamente. A string torna
a lista um campo escalar legível em consumidores GIS. Consumidores que convertem
coordenadas para float podem perder precisão; isso não altera o cálculo exato
feito aqui.

CSV é UTF-8 com LF, quoting RFC, cabeçalho
`point_id,longitude,latitude,status,zone_id,relation`. Ordem analítica, coordenadas
decimais exatas, IDs de zona vazios e relação `outside` nos pontos externos.
IDs autorais cujo primeiro caractere após espaços seja `=`, `+`, `-` ou `@`
ganham apóstrofo inicial somente no CSV. IDs originais continuam nos JSON.
Coordenadas numéricas negativas permanecem números; não são IDs autorais.

O SVG desenha todos os pontos, usa cores e formas para status e mostra até 40
rótulos de pontos por ordem de ID; rótulos acima de 24 caracteres são abreviados
com reticências. IDs completos ficam em títulos SVG mesmo
quando o rótulo não é mostrado. IDs que contêm U+FFFE/U+FFFF, proibidos no XML
1.0, aparecem integralmente como string JSON ASCII escapada no SVG; o atributo
`data-point-id-encoding="json"` identifica essa forma reversível. JSON, CSV e
fontes conservam os caracteres originais. Furos usam `fill-rule="evenodd"`; pontos
sobrepostos podem se ocultar visualmente. O texto explica essa limitação.
Somente posições de viewport são convertidas para float e arredondadas a seis
casas. Decisões geométricas e resultados numéricos não usam float. É uma figura
científica estática, sem mapa-base, fonte externa ou escala de distância.

## CLI e publicação local

```console
zonas-geojson analisar pontos.geojson zonas.geojson --saida relatorio.zip
zonas-geojson analisar pontos.geojson zonas.geojson --saida relatorio.zip --max-checks 2000000
zonas-geojson exemplo --saida exemplo.zip
zonas-geojson --help
zonas-geojson --version
```

O exemplo distribuído tem dados autorais sintéticos, sem endereços ou jurisdições
reais. O CLI lê somente arquivos regulares explícitos, recusa links simbólicos,
FIFOs e diretórios antes da leitura e aplica limite de bytes também durante a
leitura. O diretório de saída precisa existir; não é criado automaticamente.

A publicação usa staging no mesmo diretório e commit exclusivo por `os.link`,
seguido de remoção do staging. Um destino existente, inclusive symlink, é
preservado; uma corrida em que outro processo cria o destino também o preserva.
Sistemas de arquivos sem hardlink recusam a operação com erro claro, sem fallback
de sobrescrita. Erros deixam as entradas intactas. Exit 0 indica sucesso; exit 2,
recusa/erro saneado. As mensagens não repetem valores, IDs ou chaves desconhecidas
da entrada nem despejam tracebacks.
