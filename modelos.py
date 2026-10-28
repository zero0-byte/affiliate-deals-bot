from dataclasses import dataclass
from typing import Optional


@dataclass
class Oferta:
    fonte: str  # "Shopee", "AliExpress"...
    id: str
    titulo: str
    preco: float
    link: str
    preco_original: Optional[float] = None
    desconto: int = 0  # em %
    imagem: Optional[str] = None
    vendas: Optional[int] = None
    url_produto: Optional[str] = None  # link comum do produto (para gerar links por canal)
    selo: Optional[str] = None  # ex: "📉 Menor preço em 30 dias"

    @property
    def chave(self):
        return f"{self.fonte}:{self.id}"
