resource "aws_ecr_repository" "runtime" {
  name                 = var.repository_name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "runtime" {
  repository = aws_ecr_repository.runtime.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Retain only the five newest feasibility images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 5
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
