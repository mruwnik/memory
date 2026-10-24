data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "this" {
  cidr_block           = "10.42.0.0/24"
  enable_dns_hostnames = true
  tags                 = { Name = var.name }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
}

resource "aws_subnet" "this" {
  vpc_id            = aws_vpc.this.id
  cidr_block        = aws_vpc.this.cidr_block
  availability_zone = coalesce(var.availability_zone, data.aws_availability_zones.available.names[0])
  tags              = { Name = var.name }

  # The data volume is tied to this AZ: a reordered AZ list must not move it.
  lifecycle {
    ignore_changes = [availability_zone]
  }
}

resource "aws_route_table" "this" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
}

resource "aws_route_table_association" "this" {
  subnet_id      = aws_subnet.this.id
  route_table_id = aws_route_table.this.id
}

resource "aws_security_group" "this" {
  name        = var.name
  description = "HTTP and HTTPS in; admin access is SSM only"
  vpc_id      = aws_vpc.this.id

  dynamic "ingress" {
    for_each = [80, 443]
    content {
      from_port   = ingress.value
      to_port     = ingress.value
      protocol    = "tcp"
      cidr_blocks = ["0.0.0.0/0"]
    }
  }

  # Updates, image pulls, the SSM endpoints and the model APIs are all outbound.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# The address lives on the interface so it is in place before first boot and
# survives stop/start and instance replacement.
resource "aws_network_interface" "this" {
  subnet_id       = aws_subnet.this.id
  security_groups = [aws_security_group.this.id]
}

resource "aws_eip" "this" {
  domain            = "vpc"
  network_interface = aws_network_interface.this.id
  depends_on        = [aws_internet_gateway.this]
}

resource "aws_route53_record" "this" {
  count   = var.route53_zone_id == null ? 0 : 1
  zone_id = var.route53_zone_id
  name    = var.domain
  type    = "A"
  ttl     = 300
  records = [aws_eip.this.public_ip]
}
