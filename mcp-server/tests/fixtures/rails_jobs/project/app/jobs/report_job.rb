class ReportJob < ApplicationJob
  def perform(report, format)
    [report, format]
  end
end
