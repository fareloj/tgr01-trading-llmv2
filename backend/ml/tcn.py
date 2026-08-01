from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import nn


QUANTILES = (0.10, 0.50, 0.90)


@dataclass(frozen=True)
class TCNConfig:
    input_channels: int
    horizons_minutes: tuple[int, ...] = (15, 60)
    channels: int = 48
    levels: int = 6
    kernel_size: int = 3
    dropout: float = 0.10

    def __post_init__(self) -> None:
        if self.input_channels <= 0 or self.channels <= 0 or self.levels <= 0:
            raise ValueError("TCN dimensions must be positive")
        if self.kernel_size < 2:
            raise ValueError("TCN kernel_size must be at least two")
        if not 0 <= self.dropout < 1:
            raise ValueError("TCN dropout must be in [0, 1)")
        if not self.horizons_minutes or any(item <= 0 for item in self.horizons_minutes):
            raise ValueError("TCN horizons must contain positive values")

    @property
    def receptive_field(self) -> int:
        return 1 + 2 * (self.kernel_size - 1) * sum(2**level for level in range(self.levels))

    def as_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["horizons_minutes"] = list(self.horizons_minutes)
        result["receptive_field"] = self.receptive_field
        return result


class CausalConv1d(nn.Conv1d):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.causal_padding = self.padding[0]

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        result = super().forward(value)
        return result[..., : -self.causal_padding] if self.causal_padding else result


class TemporalBlock(nn.Module):
    def __init__(self, channels: int, *, kernel_size: int, dilation: int, dropout: float) -> None:
        super().__init__()
        padding = (kernel_size - 1) * dilation
        self.network = nn.Sequential(
            CausalConv1d(
                channels,
                channels,
                kernel_size,
                padding=padding,
                dilation=dilation,
            ),
            nn.GELU(),
            nn.Dropout(dropout),
            CausalConv1d(
                channels,
                channels,
                kernel_size,
                padding=padding,
                dilation=dilation,
            ),
            nn.GELU(),
            nn.Dropout(dropout),
        )
        self.normalization = nn.GroupNorm(1, channels)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return self.normalization(value + self.network(value))


class QuantileTCN(nn.Module):
    def __init__(self, config: TCNConfig) -> None:
        super().__init__()
        self.config = config
        self.input_projection = nn.Conv1d(config.input_channels, config.channels, 1)
        self.blocks = nn.Sequential(
            *[
                TemporalBlock(
                    config.channels,
                    kernel_size=config.kernel_size,
                    dilation=2**level,
                    dropout=config.dropout,
                )
                for level in range(config.levels)
            ]
        )
        self.head = nn.Sequential(
            nn.Linear(config.channels, config.channels),
            nn.GELU(),
            nn.Linear(config.channels, len(config.horizons_minutes) * len(QUANTILES)),
        )
        self.direction_head = nn.Sequential(
            nn.Linear(config.channels, config.channels),
            nn.GELU(),
            nn.Linear(config.channels, len(config.horizons_minutes) * 3),
        )

    def encode(self, value: torch.Tensor) -> torch.Tensor:
        encoded = self.blocks(self.input_projection(value))
        return encoded[..., -1]

    def forward_heads(self, value: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        encoded = self.encode(value)
        raw = self.head(encoded)
        directions = self.direction_head(encoded)
        return (
            raw.view(len(value), len(self.config.horizons_minutes), len(QUANTILES)),
            directions.view(len(value), len(self.config.horizons_minutes), 3),
        )

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        raw, _ = self.forward_heads(value)
        return raw


def quantile_loss(
    predictions: torch.Tensor,
    targets: torch.Tensor,
    *,
    crossing_penalty: float = 0.10,
) -> torch.Tensor:
    if predictions.ndim != 3 or predictions.shape[-1] != len(QUANTILES):
        raise ValueError("predictions must be [batch, horizons, quantiles]")
    if targets.shape != predictions.shape[:2]:
        raise ValueError("targets must match prediction batch and horizon dimensions")
    quantiles = predictions.new_tensor(QUANTILES).view(1, 1, -1)
    errors = targets.unsqueeze(-1) - predictions
    pinball = torch.maximum((quantiles - 1.0) * errors, quantiles * errors).mean()
    crossing = torch.relu(predictions[..., :-1] - predictions[..., 1:]).mean()
    return pinball + crossing_penalty * crossing


def ordered_quantiles(predictions: torch.Tensor) -> torch.Tensor:
    return torch.sort(predictions, dim=-1).values
