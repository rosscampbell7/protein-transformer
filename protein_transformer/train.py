import torch
import typer
import json
import functools
import pandas as pd
import numpy as np
import torch.nn as nn

from pathlib import Path
from collections import defaultdict
#from protein_transformer.data import Tokenizer, load_data, BCRDataset, collate_fn
from data import Tokenizer, load_data, BCRDataset, collate_fn # Notice the leading dot (.)
from model import AntibodyClassifier
from utils import set_seeds, get_device
from typing_extensions import Annotated
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader
from sklearn.metrics import (
    accuracy_score,
    roc_auc_score,
    precision_recall_fscore_support,
)

# ... (all your other functions: get_dataloaders, train_epoch, val_epoch) ...
def get_dataloaders(df, val_size, tokenizer, device, batch_size):
    df_train, df_val = train_test_split(
        df, stratify=df["label"], random_state=0, test_size=val_size
    )

    # reset index
    df_train.reset_index(inplace=True, drop=True)
    df_val.reset_index(inplace=True, drop=True)

    # datasets
    train_ds = BCRDataset(df_train)
    val_ds = BCRDataset(df_val)

    collate_fn_partial = functools.partial(
        collate_fn, tokenizer=tokenizer, device=device
    )

    # dataloaders
    train_dl = DataLoader(
        train_ds, collate_fn=collate_fn_partial, batch_size=batch_size, shuffle=True
    )
    val_dl = DataLoader(val_ds, collate_fn=collate_fn_partial, batch_size=batch_size)

    return train_dl, val_dl


def train_epoch(
    model: nn.Module,
    train_dl: DataLoader,
    loss_fn: nn.Module,
    opt: torch.optim.Optimizer,
) -> float:
    """
    Performs a training epoch on the model.

    Args:
        model: The PyTorch model to be trained (subclass of `torch.nn.Module`).
        train_dl: A PyTorch `DataLoader` object representing the training data.
            Each element of the dataset should be a dictionary with the following keys:
                - "input_ids": A PyTorch tensor of input token IDs.
                - "attention_mask": A PyTorch tensor of attention masks.
                - "label": A PyTorch tensor of ground truth labels.
        loss_fn: A PyTorch loss function module (subclass of `torch.nn.Module`).
            It should take the model's output (logits) and the ground truth labels as input
            and return the loss value as a tensor.
        opt: A PyTorch optimizer object used for updating the model's weights.

    Returns:
        The average loss value over the entire training epoch (float).
    """

    # Set the model to training mode
    model.train()

    total_loss = 0

    # Iterate over each batch in the training dataset
    for batch in train_dl:
        # First clear accumulated gradients
        opt.zero_grad()

        logits = model(batch)
        loss = loss_fn(logits, batch["label"])

        # Backpropagation and update
        loss.backward()
        opt.step()

        # total batch loss
        total_loss += loss.item() * batch["input_ids"].shape[0]

    return total_loss / len(train_dl.dataset)


def val_epoch(
    model: nn.Module, val_dl: DataLoader, loss_fn: nn.Module
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    """
    Performs a validation epoch on the model.

    Args:
        model: The PyTorch model to be validated (subclass of `torch.nn.Module`).
        val_dl: A PyTorch `DataLoader` object representing the validation data.
            Each element of the dataset should be a dictionary with the following keys:
                - "input_ids": A PyTorch tensor of input token IDs.
                - "attention_mask": A PyTorch tensor of attention masks.
                - "label": A PyTorch tensor of ground truth labels.
        loss_fn: A PyTorch loss function module (subclass of `torch.nn.Module`).
            It should take the model's output (logits) and the ground truth labels as input
            and return the loss value as a tensor.

    Returns:
        A tuple containing:
            - Average loss over the entire validation epoch (float).
            - Concatenated ground truth labels (numpy array).
            - Concatenated predicted labels (numpy array).
            - Stacked softmax probabilities (numpy array).
    """
    # Set the model to evaluation mod
    model.eval()

    total_loss = 0
    y_true, y_pred, y_prob = [], [], []
    with torch.inference_mode():
        for batch in val_dl:
            logits = model(batch)
            loss = loss_fn(logits, batch["label"])

            # total loss, truth, and predictions for each batch
            total_loss += loss.item() * batch["input_ids"].shape[0]
            y_true.append(batch["label"].cpu().numpy())
            y_pred.append(torch.argmax(logits, dim=-1).cpu().numpy())
            y_prob.append(torch.softmax(logits, dim=-1).cpu().numpy())

    return (
        total_loss / len(val_dl.dataset),
        np.concatenate(y_true, axis=None),
        np.concatenate(y_pred, axis=None),
        np.vstack(y_prob),
    )




def main(
    run_id: Annotated[str, typer.Option(help="Name for the training run ID")],
    dataset_loc: Annotated[
        str, typer.Option(help="Path to the dataset in parquet format")
    ],
    # ... (all the other arguments from train_model) ...
    val_size: Annotated[
        float, typer.Option(help="Proportion of the dataset to use for validation")
    ] = 0.15,
    embedding_dim: Annotated[
        int, typer.Option(help="Dimensionality of token embeddings")
    ] = 64,
    num_layers: Annotated[
        int, typer.Option(help="Number of Transformer encoder layers")
    ] = 8,
    num_heads: Annotated[
        int, typer.Option(help="Number of attention heads in the encoder")
    ] = 2,
    ffn_dim: Annotated[
        int,
        typer.Option(help="Dimensionality of the feed-forward layer in the encoder"),
    ] = 128,
    dropout: Annotated[
        float, typer.Option(help="Dropout probability for regularization")
    ] = 0.05,
    num_classes: Annotated[
        int, typer.Option(help="Number of final output dimensions")
    ] = 2,
    batch_size: Annotated[int, typer.Option(help="Number of samples per batch")] = 32,
    lr: Annotated[
        float, typer.Option(help="The learning rate for the optimizer")
    ] = 2e-5,
    num_epochs: Annotated[int, typer.Option(help="Number of epochs for training")] = 20,
    verbose: Annotated[
        bool, typer.Option(help="Whether to print verbose training messages")
    ] = True,
    output_dir: Annotated[
        str, typer.Option(help="Path to save the best model and training results")
    ] = "runs",
) -> None:
    """
    Trains a classification model using a Transformer architecture.
    """
    # (Paste the entire code body of the original train_model function here)
    # create a directory to save the model
    save_path = Path(f"{output_dir}/{run_id}")
    if not save_path.exists():
        save_path.mkdir(parents=True)

    # Dataset
    df, classes = load_data(dataset_loc)

    # save classes
    with open(save_path / "classes.json", "w") as f:
        json.dump(classes, f, indent=4, sort_keys=False)

    tokenizer = Tokenizer()
    device = get_device()

    # train and val dataloaders
    train_dl, val_dl = get_dataloaders(df, val_size, tokenizer, device, batch_size)

    # model
    model = AntibodyClassifier(
        vocab_size=tokenizer.vocab_size,
        padding_idx=tokenizer.pad_token_id,
        embedding_dim=embedding_dim,
        num_layers=num_layers,
        num_heads=num_heads,
        ffn_dim=ffn_dim,
        dropout=dropout,
        num_classes=num_classes,
    )
    model.to(device)

    loss_fn = nn.CrossEntropyLoss()
    opt = torch.optim.Adam(model.parameters(), lr=lr)

    results = defaultdict(list)

    # save best model based validation loss
    best_val_loss = float("inf")

    # save model parameters
    params = {
        "vocab_size": tokenizer.vocab_size,
        "padding_idx": tokenizer.pad_token_id,
        "embedding_dim": embedding_dim,
        "num_layers": num_layers,
        "num_heads": num_heads,
        "ffn_dim": ffn_dim,
        "dropout": dropout,
        "num_classes": num_classes,
    }
    with open(save_path / "args.json", "w") as f:
        json.dump(params, f, indent=4, sort_keys=False)

    for epoch in range(1, num_epochs + 1):
        results["Epoch"].append(epoch)
        # Train
        train_loss = train_epoch(model, train_dl, loss_fn, opt)
        # Validation
        val_loss, y_true, y_pred, y_prob = val_epoch(model, val_dl, loss_fn)

        results["train_loss"].append(train_loss)
        results["val_loss"].append(val_loss)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            # save best model
            torch.save(model.state_dict(), save_path / f"best_model.pt")

        # accuracy
        results["val_accuracy"].append(accuracy_score(y_true, y_pred))

        # auc score
        if num_classes == 2:
            auc_score = roc_auc_score(y_true, y_prob[:, 1])
            results["val_auc"].append(auc_score)

        precision, recall, f1, _ = precision_recall_fscore_support(
            y_true, y_pred, average="weighted"
        )

        results["val_precision"].append(precision)
        results["val_recall"].append(recall)
        results["val_f1"].append(f1)

        if verbose:
            print(
                f"Epoch {epoch}: Train Loss: {train_loss}, "
                f"Valid Loss: {val_loss}, "
                f"Valid AUC Score: {auc_score}"
            )

    # Save training results as CSV
    results = pd.DataFrame(results)
    results.to_csv(save_path / "results.csv", index=False)
    

if __name__ == "__main__":
    set_seeds()
    typer.run(main)
