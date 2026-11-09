# API e geometria exata

```python
from zonas_geojson import analyze
from zonas_geojson.jsoncodec import loads

pontos = loads(pontos_json)  # bytes UTF-8 ou str
zonas = loads(zonas_json)
resultado = analyze(pontos, zonas, max_checks=2_000_000)
analise = resultado.to_dict()
anotados = resultado.annotated_points()
```

`analyze(points_document, zones_document, *, max_checks=2_000_000)` valida
integralmente as duas fontes e retorna `JoinResult`. A API revalida também os
objetos já carregados pelo codec. Aceita somente valores JSON comuns: `dict`,
`list`, `str`, `int`, `Decimal`, `bool` e `None`; subclasses e objetos com
comportamento próprio são recusados. Coordenadas aceitam apenas `int` e
`Decimal`, sem `bool` ou `float`.

`JoinResult` guarda árvores imutáveis, sem aliases mutáveis das entradas.
`to_dict()`, `source_points()`, `source_zones()` e `annotated_points()` devolvem
novos dicionários e listas a cada chamada. Alterar uma cópia ou as entradas não
altera o resultado. A API conserva os valores e tipos numéricos exatos das
fontes; a serialização JSON pode normalizar a representação decimal.

## Recorte das fontes

Cada documento é uma `FeatureCollection` com somente `type` e `features`.
Cada `Feature` tem somente `type`, `id`, `properties` e `geometry`; cada geometria
tem somente `type` e `coordinates`. Os pontos são `Point` 2D. As zonas são
`Polygon` ou `MultiPolygon`. `properties` é um objeto JSON, inclusive vazio.
Não são aceitos `bbox`, `crs`, geometria nula, altitude ou membros extras.

IDs são textos de 1 a 80 pontos Unicode, únicos em cada coleção, sem espaços
externos, C0, DEL, surrogates ou U+2028/U+2029. Propriedades não substituem o ID.
Pontos recusam os nomes `zg_point_id`, `zg_status`, `zg_count`, `zg_boundary` e
`zg_matches`, reservados para a exportação anotada. Zonas podem usá-los como
metadados. Nenhum metadado participa da classificação.

Longitude pertence a `[-180,180]`; latitude pertence a `[-90,90]`. Cada valor
normalizado tem até 18 casas decimais: zeros decimais finais não aumentam essa
contagem. Não há conversão para `float`, epsilon, projeção, cálculo de área real
ou distância. Os segmentos são cartesianos em graus. A exatidão se refere aos
decimais fornecidos, sem afirmar precisão física das coordenadas.

As entradas têm os limites do codec: 8 MiB por documento em JSON canônico com
indentação 2 e LF, profundidade 16, 200000 nós, até 40 dígitos na mantissa,
expoente explícito entre -24 e 24 e lexema numérico de até 64 caracteres. A CLI
limita também os bytes originais. Há no máximo 10000 pontos, 100 zonas e 5000
posições nas zonas, contando os fechamentos. Cada anel tem de 4 a 1000 posições;
cada polígono pode ter até 64 furos. Coleções vazias são válidas.

Cada anel precisa estar fechado, ter área algébrica não nula e não repetir
vértices, salvo o fechamento. Bordas não adjacentes não podem cruzar, tocar ou
se sobrepor. Bordas adjacentes podem continuar na mesma reta, compartilhando
somente o vértice; retornos com sobreposição são recusados. Furos ficam
estritamente dentro do exterior, sem encontros entre bordas ou furos aninhados.
Os componentes de um `MultiPolygon` precisam ter superfícies e bordas separadas;
uma ilha dentro de um furo pode ser um componente separado. A orientação dos
anéis não muda o resultado.

Cada anel tem amplitude de longitude menor que 180 graus. Anéis que exigiriam
atravessar o antimeridiano são recusados. Não há normalização entre -180 e 180.
Componentes previamente recortados nos dois lados podem coexistir. Zonas
diferentes podem compartilhar bordas ou se sobrepor livremente.

## Relações e resultado

O motor usa `Fraction`, determinante de orientação e teste de segmento para
fronteiras. O cruzamento do raio usa um intervalo vertical semiaberto: os dois
extremos de um segmento entram em lados opostos do teste somente quando
`(ay > py) != (by > py)`. Assim, vértices são contados uma única vez. O sinal do
determinante decide de que lado do raio está o cruzamento, sem divisão ou
arredondamento.

Um par ponto–zona recebe `inside` no interior do exterior e fora dos furos;
`boundary` sobre qualquer borda, inclusive a borda de um furo. Os demais pares
não aparecem em `matches`. `MultiPolygon` representa a união de seus
componentes. Não se escolhe uma zona vencedora.

`to_dict()` contém:

- `version: 1` e `algorithm: "exact-ray-crossing"`;
- `summary`: `pointCount`, `zoneCount`, `matchCount`, `insideCount`,
  `boundaryCount`, `ambiguousCount`, `outsideCount`, `pointsWithBoundary`;
- `points`: `id`, `coordinates`, `status`, `matches` por ponto; cada match tem
  `zoneId` e `relation`;
- `zones`: `id`, `pointCount`, `insideCount`, `boundaryCount` por zona;
- `work`: `checks`, `limit`, `topologyChecks`, `joinChecks`, `bboxRejectedPairs`.

Pontos e zonas do relatório são ordenados pelo ID, na ordem Unicode de Python;
os matches são ordenados pelo ID da zona. Zero matches gera `outside`; um único
match gera `inside` ou `boundary`; dois ou mais geram `ambiguous`. Esses quatro
totais são disjuntos e somam `pointCount`. `pointsWithBoundary` inclui também
pontos ambíguos que tenham alguma relação de fronteira. O limite é de 100000
matches por chamada; ultrapassá-lo recusa a análise inteira.

`source_points()` e `source_zones()` conservam a ordem das feições das fontes.
`annotated_points()` conserva essa ordem, as coordenadas e as propriedades dos
pontos, acrescentando os cinco campos reservados. `zg_count` conta matches;
`zg_boundary` indica qualquer match de fronteira. `zg_matches` é um **texto JSON
compacto**, com Unicode preservado, contendo os objetos `zoneId`/`relation`.
Consumidores devem interpretar esse campo explicitamente para recuperar a lista.

## Orçamento verificável

`max_checks` é um inteiro de 1 a 20000000; o padrão é 2000000. O mesmo orçamento
cobre a validação topológica e a junção, nesta ordem. Uma unidade corresponde a:

1. um teste de interseção de dois segmentos cujas caixas delimitadoras se
   encontram, durante a validação topológica; ou
2. uma visita a um segmento durante a localização de um ponto, incluindo o teste
   de fronteira e, quando aplicável, a regra de cruzamento do raio.

`topologyChecks` soma os testes de interseção e as visitas de localização usados
para verificar contenção de furos e separação de componentes. `joinChecks` soma
as visitas de localização da junção ponto–zona. Uma localização pode terminar
ao encontrar uma fronteira; um exterior fora do ponto dispensa seus furos.

Caixas delimitadoras eliminam pares de segmentos na validação e pares
ponto–zona na junção, sem consumir checks. `bboxRejectedPairs` conta **somente
pares ponto–zona** rejeitados pela caixa da zona; está entre zero e
`pointCount * zoneCount`. Caixas de pares de segmentos não entram nesse campo.
Não se faz poda por caixa de anel durante a localização.

`checks == topologyChecks + joinChecks <= limit`. A ordem da validação segue as
fontes; a junção segue os IDs ordenados. As métricas descrevem esse trabalho
lógico, sem prometer limite absoluto de tempo ou RAM. Exemplo mínimo: um
quadrado, um ponto interior, um ponto no primeiro vértice e um ponto fora da
caixa consomem 4 checks topológicos e 5 de junção; `bboxRejectedPairs` é 1.

Ao exceder o orçamento ou qualquer limite, nenhum resultado parcial é
retornado. Entradas não são modificadas.

## Erros

`ZoneError` deriva de `ValueError` e oferece `code`, `message` e `path`. A mensagem
em português não repete valores, IDs nem chaves desconhecidas. `path`, quando
presente, usa apenas nomes do esquema e índices numéricos.

| Código do núcleo | Motivo |
| --- | --- |
| `INPUT_SCHEMA` | Estrutura, tipo de geometria ou propriedade reservada incompatível. |
| `INPUT_ID` | ID inválido ou duplicado na coleção. |
| `INPUT_LIMIT` | Quantidade de feições/vértices ou configuração do orçamento inválida. |
| `INPUT_COORDINATE` | Posição, tipo, intervalo ou casas decimais de coordenada inválidos. |
| `INPUT_TOPOLOGY` | Anel, furo ou componente fora do recorte topológico. |
| `WORK_LIMIT` | Orçamento lógico de verificações excedido. |
| `MATCH_LIMIT` | Mais de 100000 correspondências. |

O codec também pode recusar a fonte com `JSON_INVALID`, `JSON_LIMIT` ou
`JSON_DUPLICATE_KEY` antes da validação geométrica. Falhas são integrais, e dados
sensíveis das fontes não são interpolados nas mensagens.
