module Zed
  class Outer::Inner < ApplicationRecord
    belongs_to :user, class_name: "User"
    self.table_name = "zed_inner"
  end
end
