"""CPU inference for the Digital Agency kanjikana 1.9o checkpoint.

Architecture and beam search adapted from digital-go-jp/kanjikana-model
(MIT License, Copyright (c) 2024 デジタル庁).
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import torch
from torch import nn

MODEL_VERSION = "kanjikana-1.9o"
MODEL_SHA256 = "cd9d29ad7ecf33afb02a14b807716fbf07fc6d535b9fa385bc431cdf7e53850f"
BOS, EOS, UNK = 2, 3, 0


class PositionalEncoding(nn.Module):
    def __init__(self, size: int, dropout: float):
        super().__init__()
        den = torch.exp(-torch.arange(0, size, 2) * math.log(10000) / size)
        positions = torch.arange(5000).reshape(5000, 1)
        embedding = torch.zeros((5000, size))
        embedding[:, 0::2] = torch.sin(positions * den)
        embedding[:, 1::2] = torch.cos(positions * den)
        self.register_buffer("pos_embedding", embedding.unsqueeze(-2))
        self.dropout = nn.Dropout(dropout)

    def forward(self, tokens: torch.Tensor) -> torch.Tensor:
        return self.dropout(tokens + self.pos_embedding[:tokens.size(0), :])


class TokenEmbedding(nn.Module):
    def __init__(self, count: int, size: int):
        super().__init__()
        self.embedding = nn.Embedding(count, size)
        self.emb_size = size

    def forward(self, tokens: torch.Tensor) -> torch.Tensor:
        return self.embedding(tokens.long()) * math.sqrt(self.emb_size)


class NameTransformer(nn.Module):
    def __init__(self, params: dict, source_size: int, target_size: int):
        super().__init__()
        size = params["emb_size"]
        self.transformer = nn.Transformer(
            d_model=size,
            nhead=params["nhead"],
            num_encoder_layers=params["num_encoder_layers"],
            num_decoder_layers=params["num_decoder_layers"],
            dim_feedforward=params["ffn_hid_dim"],
            dropout=params["dropout"],
        )
        self.generator = nn.Linear(size, target_size)
        self.src_tok_emb = TokenEmbedding(source_size, size)
        self.tgt_tok_emb = TokenEmbedding(target_size, size)
        self.positional_encoding = PositionalEncoding(size, params["dropout"])

    def encode(self, source: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        return self.transformer.encoder(self.positional_encoding(self.src_tok_emb(source)), mask)

    def decode(self, target: torch.Tensor, memory: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        return self.transformer.decoder(self.positional_encoding(self.tgt_tok_emb(target)), memory, mask)


class KanaModel:
    def __init__(self, checkpoint_path: Path):
        with checkpoint_path.open("rb") as checkpoint_file:
            digest = hashlib.file_digest(checkpoint_file, "sha256").hexdigest()
        if digest != MODEL_SHA256:
            raise ValueError(f"Unexpected kanjikana checkpoint SHA-256: {digest}")
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
        self.source_vocab = {char: index for index, char in enumerate(checkpoint["src_vocab"])}
        self.target_vocab = checkpoint["tgt_vocab"]
        self.model = NameTransformer(checkpoint["params"], len(self.source_vocab), len(self.target_vocab))
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.model.eval()
        del checkpoint

    @torch.inference_mode()
    def predict(self, name: str, count: int = 5) -> list[dict]:
        if not name or len(name) > 30 or any(char not in self.source_vocab for char in name):
            return []
        source = torch.tensor([BOS, *(self.source_vocab.get(char, UNK) for char in name), EOS]).view(-1, 1)
        memory = self.model.encode(source, torch.zeros(len(source), len(source), dtype=torch.bool))
        beams = [([BOS], 0.0)]
        for _ in range(min(40, len(name) * 4 + 8)):
            next_beams = []
            for tokens, log_probability in beams:
                if tokens[-1] == EOS:
                    next_beams.append((tokens, log_probability))
                    continue
                target = torch.tensor(tokens).view(-1, 1)
                mask = torch.triu(torch.ones(len(tokens), len(tokens), dtype=torch.bool), diagonal=1)
                decoded = self.model.decode(target, memory, mask)
                logits = self.model.generator(decoded[-1])
                values, indices = torch.topk(torch.log_softmax(logits, dim=1)[0], count)
                for value, index in zip(values.tolist(), indices.tolist()):
                    next_beams.append((tokens + [index], log_probability + value))
            # Match the reference beam search length penalty.
            beams = sorted(next_beams, key=lambda item: item[1] / (((6 + len(item[0]) - 1) / 6) ** 0.6), reverse=True)[:count]
            if all(tokens[-1] == EOS for tokens, _ in beams):
                break
        results = []
        for tokens, log_probability in beams:
            if tokens[-1] != EOS:
                continue
            reading = "".join(self.target_vocab[index] for index in tokens[1:-1])
            if reading and all("ァ" <= char <= "ヶ" or char == "ー" for char in reading):
                results.append({"reading": reading, "log_probability": log_probability})
        return results
