output "instance_public_ip" {
  description = "Public IP of the EC2 instance"
  value       = module.vpc_instance.instance_public_ip
}

output "instance_public_dns" {
  description = "Public DNS of the EC2 instance"
  value       = module.vpc_instance.instance_public_dns
}

output "ssh_command" {
  description = "SSH command to connect to the instance"
  value       = "ssh -i ${module.ssh_key.priv_key_file} ubuntu@${module.vpc_instance.instance_public_dns}"
}

output "ssh_alias_command" {
  description = "Command to add SSH alias to your shell"
  value       = "echo 'alias ssh-strategies=\"ssh -i ${module.ssh_key.priv_key_file} ubuntu@${module.vpc_instance.instance_public_dns}\"' >> ~/.bashrc && source ~/.bashrc"
}