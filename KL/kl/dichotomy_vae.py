import torch
import torch.nn as nn
from enum import Enum
import random
import numpy as np
def set_seed(seed):
    """
    Set seeds for reproducibility.
    """
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False




# Encoder network
class Encoder(nn.Module):
    def __init__(self, input_dim, latent_dim):
        super(Encoder, self).__init__()
        self.fc1 = nn.Linear(input_dim, 128)
        self.fc2_mean = nn.Linear(128, latent_dim)
        self.fc2_logvar = nn.Linear(128, latent_dim)

    def forward(self, x):
        h = torch.relu(self.fc1(x))
        z_mean = self.fc2_mean(h)
        z_logvar = self.fc2_logvar(h)
        return z_mean, z_logvar

# Decoder network
class Decoder(nn.Module):
    def __init__(self, latent_dim, output_dim):
        super(Decoder, self).__init__()
        self.fc1 = nn.Linear(latent_dim, 128)
        self.fc2 = nn.Linear(128, output_dim)

    def forward(self, z):
        h = torch.relu(self.fc1(z))
        x_recon = torch.sigmoid(self.fc2(h))  # Use sigmoid for binary data
        return x_recon

class Classifier(nn.Module):
    def __init__(self, latent_dim, num_classes):
        super(Classifier, self).__init__()
        self.fc1 = nn.Linear(latent_dim, 64)
        self.fc2 = nn.Linear(64, 32)
        self.fc3 = nn.Linear(32, num_classes)

    def forward(self, z):
        h = torch.relu(self.fc1(z))
        h = torch.relu(self.fc2(h))
        return self.fc3(h)  # No softmax, CrossEntropyLoss applies it


class LossType(Enum):
    Total = 0
    Classifier = 1
    Reconstruction = 2
    KL = 3
    ClassifierReconstruction = 4

class DichotomyVAE(nn.Module):
    def __init__(self, input_dim, output_dim, latent_dim, num_classes, loss_type = LossType.Classifier, verbose=False):
        super(DichotomyVAE, self).__init__()
        set_seed(42) # For Reproducibility
        self.encoder = Encoder(input_dim, latent_dim)
        self.decoder = Decoder(latent_dim, output_dim)
        self.classifier = Classifier(latent_dim, num_classes)
        self.verbose = verbose
        self.loss_type = loss_type
        self.is_fitted = False  # A flag to track whether the model is trained

        # Automatically set the device based on availability (CUDA, MPS, or CPU)
        if torch.cuda.is_available():
            self.device = torch.device("cuda")
            if self.verbose:
                print("Using CUDA for GPU acceleration")
        # MPS is too slow
        # elif torch.backends.mps.is_available():
        #     self.device = torch.device("mps")
        #     if self.verbose:
        #         print("Using MPS (Apple Silicon) for GPU acceleration")
        else:
            self.device = torch.device("cpu")
            if self.verbose:
                print("Using CPU")

        # Move the model to the selected device
        self.to(self.device)

    def reparameterize(self, z_mean, z_logvar):
        std = torch.exp(0.5 * z_logvar)
        eps = torch.randn_like(std)
        return z_mean + eps * std

    def forward(self, x):
        x = x.to(self.device)  # Ensure input data is on the correct device
        z_mean, z_logvar = self.encoder(x)
        latent = self.reparameterize(z_mean, z_logvar)
        x_recon = self.decoder(latent)
        y_pred = self.classifier(latent)  # Predicted class labels from latent space
        return x_recon, z_mean, z_logvar, y_pred, latent

    def fit(self, dataloader, optimizer, scheduler, num_epochs=10, beta=1, lambda_class=1):
        """
        Fit the VAE model using the provided data and optimizer.
        """
        self.train()
        for epoch in range(num_epochs):
            total_loss = 0
            for batch_idx, (x, y) in enumerate(dataloader):
                # Move data to the selected device
                x, y = x.to(self.device), y.to(self.device)

                optimizer.zero_grad()
                x_recon, z_mean, z_logvar, y_pred, latent = self(x)

                total_loss, recon_loss, kl_loss, class_loss, kl_reverse = self.loss_function(
                    x_recon, x, z_mean, z_logvar, y_pred, y, beta, lambda_class)

                if self.loss_type == LossType.Total:
                    loss = total_loss
                elif self.loss_type == LossType.Classifier:
                    loss = class_loss + 1/kl_loss/1000/(epoch+1)
                elif self.loss_type == LossType.Reconstruction:
                    loss = recon_loss
                elif self.loss_type == LossType.KL:
                    loss = kl_loss
                elif self.loss_type == LossType.ClassifierReconstruction:
                    loss = class_loss + recon_loss
                else:
                    loss = class_loss  # Modify if you want to use total_loss

                loss.backward()
                optimizer.step()
                total_loss += loss.item()

            if epoch % 100 == 0 and self.verbose:
                print(
                    f'Epoch {epoch:10}, kl: {kl_loss:.4f}, class: {class_loss:.4f}, lr: {scheduler.get_last_lr()[0]:.6f}')
            scheduler.step()

        self.is_fitted = True  # Mark the model as fitted

    def transform(self, X):
        """
        Apply the trained model to input data X and return the latent representation and reconstructed data.
        """
        if not self.is_fitted:
            raise RuntimeError("DichotomyVAE is not fitted yet. Call `fit` first.")

        self.eval()
        X = X.to(self.device)  # Move data to the selected device
        with torch.no_grad():
            z_mean, z_logvar = self.encoder(X)
            latent = self.reparameterize(z_mean, z_logvar)
            x_recon = self.decoder(latent)
            # Predict class from the latent vector z (optional)
            y_pred = self.classifier(latent)
            predicted_class = torch.argmax(y_pred, dim=1)  # Get the class with highest probability

        return x_recon, latent, predicted_class  # Return the reconstructed data and latent space

    def fit_transform(self, dataloader, optimizer, scheduler, num_epochs=10, beta=1, lambda_class=1):
        """
        Combines fit and transform: fits the model and returns the transformed data.
        """
        self.fit(dataloader, optimizer, scheduler, num_epochs=num_epochs, beta=beta, lambda_class=lambda_class)
        all_data = next(iter(dataloader))[0]  # Extracts the input data from the first batch
        return self.transform(all_data)  # Return transformed data after fitting

    @staticmethod
    def loss_function(x_recon, x, z_mean, z_logvar, y_pred, y, beta=1, lambda_class=1):
        # Reconstruction loss (e.g., MSE or BCE)
        recon_loss = nn.functional.mse_loss(x_recon, x, reduction='sum')

        # KL divergence loss
        kl_loss = -0.5 * torch.sum(1 + z_logvar - z_mean.pow(2) - z_logvar.exp())

        # Reverse KL divergence loss (hypothetical for monitoring)
        prior_mean = torch.zeros_like(z_mean)
        prior_logvar = torch.zeros_like(z_logvar)
        kl_reverse = -0.5 * torch.sum(1 + prior_logvar - prior_mean.pow(2) - prior_logvar.exp())

        # Classification loss (cross-entropy)
        class_loss = nn.functional.cross_entropy(y_pred, y)

        # Total loss
        total_loss = recon_loss + beta * kl_loss + lambda_class * class_loss
        return total_loss, recon_loss, kl_loss, class_loss, kl_reverse