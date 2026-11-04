class Admin < User
  has_many :audits, class_name: "User"
  self.table_name = "admins"
end
