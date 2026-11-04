module Legacy
  class Order < ApplicationRecord
    self.table_name = "orders_a"
    self.table_name = "orders_b"
  end
end
