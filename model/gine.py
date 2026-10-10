"""GINE с обновлением рёбер и передачей сообщений в обе стороны (раздел 3.4).

Идеи взяты из IBM Multi-GNN (Egressy et al., «Provably Powerful Graph Neural Networks for
Directed Multigraphs», AAAI 2024; github.com/IBM/Multi-GNN, Apache-2.0): обратная передача
сообщений и обновление эмбеддингов рёбер. Код написан заново под эту задачу.

Выходы: логит перевода (отмывание), логит счёта («транзитный»), 8-мерный эмбеддинг счёта
для выпусков L2/L3 (tanh, норма ограничивается шлюзом)."""

from __future__ import annotations

import torch
from torch import nn
from torch_geometric.nn import GINEConv

EMB_DIM = 8


def mlp(i: int, h: int, o: int) -> nn.Sequential:
    return nn.Sequential(nn.Linear(i, h), nn.ReLU(), nn.Linear(h, o))


class EdgeGNN(nn.Module):
    def __init__(self, node_in: int, edge_in: int, hidden: int = 64, layers: int = 2, dropout: float = 0.1):
        super().__init__()
        self.node_enc = nn.Linear(node_in, hidden)
        self.edge_enc = nn.Linear(edge_in, hidden)
        self.fwd = nn.ModuleList(GINEConv(mlp(hidden, hidden, hidden), edge_dim=hidden) for _ in range(layers))
        self.bwd = nn.ModuleList(GINEConv(mlp(hidden, hidden, hidden), edge_dim=hidden) for _ in range(layers))
        self.node_norm = nn.ModuleList(nn.BatchNorm1d(hidden) for _ in range(layers))
        self.edge_upd = nn.ModuleList(mlp(3 * hidden, hidden, hidden) for _ in range(layers))
        self.dropout = nn.Dropout(dropout)
        self.edge_head = mlp(3 * hidden, hidden, 1)
        self.node_head = nn.Linear(hidden, 1)
        self.emb_head = nn.Linear(hidden, EMB_DIM)

    def forward(self, x, edge_index, edge_attr):
        h = self.node_enc(x)
        e = self.edge_enc(edge_attr)
        src, dst = edge_index
        rev = edge_index.flip(0)
        for fwd, bwd, norm, upd in zip(self.fwd, self.bwd, self.node_norm, self.edge_upd):
            m = (fwd(h, edge_index, e) + bwd(h, rev, e)) / 2
            h = h + self.dropout(torch.relu(norm(m)))
            e = e + self.dropout(upd(torch.cat([h[src], h[dst], e], dim=1)))
        edge_logit = self.edge_head(torch.cat([h[src], h[dst], e], dim=1)).squeeze(-1)
        return edge_logit, self.node_head(h).squeeze(-1), torch.tanh(self.emb_head(h))
