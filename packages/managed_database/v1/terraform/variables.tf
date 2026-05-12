variable "db_engine" {
  description = "Database engine. One of: postgres, mysql. Mapped from request.config.engine."
  type        = string
}

variable "db_size" {
  description = "Logical size class. One of: small, medium, large, xlarge. Mapped from request.config.size."
  type        = string
}

variable "db_storage_gb" {
  description = "Initial allocated storage in gigabytes. Mapped from request.config.storage_gb."
  type        = number
}
