{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = [
    pkgs.ffmpeg
    # Add other dependencies here (e.g., pkgs.nodejs)
  ];
}