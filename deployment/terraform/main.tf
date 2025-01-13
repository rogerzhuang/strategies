terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

module "vpc_instance" {
  source       = "./modules/vpc_instance"
  project_name = "strategies"
  region       = "us-west-2"  # Change as needed
  vpc_cidr     = "10.0.0.0/16"
  instance_type = "t2.medium"  # Change based on your needs
  ami_id       = "ami-03f65b8614a860c29"  # Ubuntu AMI, change as needed
  key_name     = module.ssh_key.ssh_key_name
  root_volume_size = 20  # Adjust this value as needed
}

module "ssh_key" {
  source       = "git::https://gitlab.com/acit_4640_library/tf_modules/aws_ssh_key_pair.git"
  ssh_key_name = "strategies-key"
  output_dir   = "${path.root}/"
}

